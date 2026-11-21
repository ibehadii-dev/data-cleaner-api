"""Pure Polars-based CSV cleaning transforms.

Nothing in this module knows about HTTP; all functions take bytes/DataFrames
and return DataFrames plus plain counters.
"""

from __future__ import annotations

import csv
import io
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass

import polars as pl

from ..schemas.clean import CleanOptions
from ..schemas.common import QuarantinedRecord, SummaryReport, WarningSummary
from ..utils.dates import AMBIGUOUS_DATE_FORMATS, UNAMBIGUOUS_DATE_FORMATS
from ..utils.errors import CsvReadError

MAX_FILE_BYTES = 25 * 1024 * 1024
_TRIM_COL = "__clean_date_trimmed__"
_PARSED_COL = "__clean_date_parsed__"
_KEY_COL = "__dedupe_key__"
_ORDER_COL = "__dedupe_order__"

# Delimiters we accept explicitly or sniff from the header.
CANDIDATE_DELIMITERS: tuple[str, ...] = (",", ";", "\t", "|")


@dataclass
class CleaningOutcome:
    frame: pl.DataFrame
    summary: SummaryReport
    ignored_columns: list[str]
    quarantined: list[QuarantinedRecord]


@dataclass
class CsvReadResult:
    """A parsed frame plus the raw row accounting needed to reconcile it."""

    frame: pl.DataFrame
    quarantined: list[QuarantinedRecord]
    rows_in: int
    blank_rows_skipped: int
    delimiter: str


@dataclass
class _ParsedRecords:
    """Header, rewritten data text, and counters from the CSV scanner."""

    header: list[str]
    text: str
    quarantined: list[QuarantinedRecord]
    rows_in: int
    blank_rows_skipped: int
    delimiter: str


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
def decode_csv_bytes(data: bytes) -> str:
    """Decode uploaded bytes to text, raising CsvReadError on bad input.

    ``utf-8-sig`` strips an optional BOM. Windows ``cp1252`` is tried before
    the Latin-1 last resort so smart quotes and typographic punctuation decode
    into real characters instead of C1 control bytes. Empty / BOM-only files
    return an empty string. Upload size limiting is the router's job: it
    rejects oversized files before buffering them.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise CsvReadError("Uploaded file must be a binary file.")
    data = bytes(data)

    if not data:
        return ""

    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            # Windows exports use cp1252: smart quotes (0x93/0x94), dashes,
            # ellipses, etc. Latin-1 would silently turn these into controls.
            text = data.decode("cp1252")
        except UnicodeDecodeError:
            try:
                # Latin-1 maps every byte to a code point; it is the last
                # resort for legacy files containing cp1252's five gaps.
                text = data.decode("latin-1")
            except UnicodeDecodeError as exc:  # defensive: practically unreachable
                raise CsvReadError(
                    "Unreadable CSV file: could not decode as UTF-8, "
                    "Windows-1252 or Latin-1."
                ) from exc

    if "\x00" in text:
        raise CsvReadError("Unreadable CSV file: binary content detected.")
    if not text.strip():
        return ""
    return text


def _first_record(text: str, delimiter: str) -> list[str]:
    """Return the first parsed record of ``text`` for one delimiter."""
    try:
        return next(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    except StopIteration:
        return []
    except csv.Error as exc:
        raise CsvReadError(f"Unreadable CSV file: {exc}") from exc


def _detect_delimiter(text: str, explicit: str | None) -> str:
    """Use an explicit delimiter when supplied, otherwise sniff the header.

    The candidate producing the most header fields wins, with ties going to
    the earlier candidate (so comma is preferred over the exotic ones).
    """
    if explicit:
        return explicit
    best, best_count = ",", 0
    for candidate in CANDIDATE_DELIMITERS:
        header = _first_record(text, candidate)
        if len(header) > best_count:
            best, best_count = candidate, len(header)
    return best


def _is_blank_row(row: list[str]) -> bool:
    """True for a csv.reader empty line or a whitespace-only single field."""
    return not row or (len(row) == 1 and not row[0].strip())


def _filter_malformed_rows(text: str, delimiter: str | None = None) -> _ParsedRecords:
    """Scan the CSV and stream well-formed rows into a clean text buffer.

    Blank records are skipped and counted; ragged records are quarantined with
    a reason. Keeping only one row in memory at a time (rather than
    ``list(csv.reader(...))``) keeps peak memory close to the upload size.
    Duplicate or delimiter-conflicting headers are rejected with CsvReadError,
    which the app maps to HTTP 400.
    """
    chosen = _detect_delimiter(text, delimiter)

    try:
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=chosen)
        raw_header = next(reader)
    except StopIteration:
        return _ParsedRecords([], "", [], 0, 0, chosen)
    except csv.Error as exc:
        raise CsvReadError(f"Unreadable CSV file: {exc}") from exc

    header = [cell.strip() for cell in raw_header]
    if not header:
        return _ParsedRecords([], "", [], 0, 0, chosen)

    duplicates = sorted({name for name in header if header.count(name) > 1})
    if duplicates:
        raise CsvReadError(
            "Unreadable CSV file: duplicate column headers: "
            + ", ".join(duplicates)
        )

    # A one-column header containing a delimiter means the wrong separator was
    # chosen. Never let this parse "successfully" as a single ignored column.
    if len(header) == 1 and any(marker in header[0] for marker in (";", "\t")):
        found = "tab" if "\t" in header[0] else ";"
        raise CsvReadError(
            "Unreadable CSV file: single-column header contains a "
            f"delimiter ({found}); supply a matching 'delimiter' option or a "
            "properly delimited header."
        )

    expected = len(header)
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=chosen, lineterminator="\n")
    writer.writerow(header)

    quarantined: list[QuarantinedRecord] = []
    rows_in = 0
    blank_rows_skipped = 0

    try:
        for row in reader:
            rows_in += 1
            if _is_blank_row(row):
                blank_rows_skipped += 1
                continue
            if len(row) != expected:
                quarantined.append(
                    QuarantinedRecord(
                        row_index=rows_in - 1,
                        record=row,
                        errors=[
                            f"malformed row: expected {expected} columns, "
                            f"found {len(row)}"
                        ],
                    )
                )
                continue
            writer.writerow(row)
    except csv.Error as exc:
        raise CsvReadError(f"Unreadable CSV file: {exc}") from exc

    return _ParsedRecords(
        header=header,
        text=buffer.getvalue(),
        quarantined=quarantined,
        rows_in=rows_in,
        blank_rows_skipped=blank_rows_skipped,
        delimiter=chosen,
    )


def read_csv_with_report(
    data: bytes, delimiter: str | None = None
) -> CsvReadResult:
    """Parse CSV bytes into a reconciled frame plus raw row accounting."""
    text = decode_csv_bytes(data)
    if not text:
        return CsvReadResult(pl.DataFrame(), [], 0, 0, delimiter or ",")

    parsed = _filter_malformed_rows(text, delimiter)
    del text

    if not parsed.text:
        return CsvReadResult(
            pl.DataFrame(),
            parsed.quarantined,
            parsed.rows_in,
            parsed.blank_rows_skipped,
            parsed.delimiter,
        )

    clean_text = parsed.text
    try:
        frame = pl.read_csv(
            io.BytesIO(clean_text.encode("utf-8")),
            infer_schema_length=0,  # read every column as String
            truncate_ragged_lines=True,  # defensive; ragged rows filtered above
            separator=parsed.delimiter,
        )
    except Exception as exc:  # Polars raises several distinct exception types
        raise CsvReadError(f"Unreadable CSV file: {exc}") from exc
    del clean_text

    # Strip header whitespace so email/date/dedupe options match their columns.
    frame = frame.rename({c: c.strip() for c in frame.columns})

    # Shape check on the frame itself: no per-row Python materialisation.
    if frame.width != len(parsed.header):
        raise CsvReadError(
            "Unreadable CSV file: column count mismatch: parsed "
            f"{frame.width} columns but the header has {len(parsed.header)}."
        )

    # Reconcile against the *input* records, never against the frame alone.
    expected = parsed.rows_in - len(parsed.quarantined) - parsed.blank_rows_skipped
    if frame.height != expected:
        raise CsvReadError(
            "Unreadable CSV file: row count mismatch: parsed "
            f"{frame.height} rows but expected {expected} "
            f"(records={parsed.rows_in}, quarantined="
            f"{len(parsed.quarantined)}, blank_rows_skipped="
            f"{parsed.blank_rows_skipped})."
        )

    return CsvReadResult(
        frame=frame,
        quarantined=parsed.quarantined,
        rows_in=parsed.rows_in,
        blank_rows_skipped=parsed.blank_rows_skipped,
        delimiter=parsed.delimiter,
    )


def read_csv_bytes_with_quarantine(
    data: bytes, delimiter: str | None = None
) -> tuple[pl.DataFrame, list[QuarantinedRecord]]:
    """Parse CSV bytes, separating malformed rows from the Polars frame."""
    result = read_csv_with_report(data, delimiter)
    return result.frame, result.quarantined


def read_csv_bytes(data: bytes) -> pl.DataFrame:
    """Decode and parse uploaded CSV bytes without ever crashing the caller.

    Malformed (ragged) rows are excluded from the frame; use
    :func:`read_csv_bytes_with_quarantine` to also get their details.
    """
    frame, _ = read_csv_bytes_with_quarantine(data)
    return frame


# --------------------------------------------------------------------------- #
# Transforms
# --------------------------------------------------------------------------- #
def normalize_emails(
    frame: pl.DataFrame, columns: list[str]
) -> tuple[pl.DataFrame, int, list[str]]:
    """Trim and lowercase the given string columns; count changed cells."""
    ignored: list[str] = []
    changed_count = 0

    for column in columns:
        if column not in frame.columns:
            ignored.append(column)
            continue
        original = pl.col(column).cast(pl.String)
        cleaned = original.str.strip_chars().str.to_lowercase()
        changed = (cleaned != original).fill_null(False)
        changed_count += int(frame.select(changed.sum()).item() or 0)
        frame = frame.with_columns(cleaned.alias(column))

    return frame, changed_count, ignored


def _date_exprs(
    expression: pl.Expr, formats: Sequence[str]
) -> tuple[pl.Expr, pl.Expr]:
    """Build the trimmed-string and parsed-Date expressions for a column."""
    trimmed = expression.cast(pl.String).str.strip_chars()
    trimmed = pl.when(trimmed == "").then(None).otherwise(trimmed)

    # strict=False makes an unparseable value null instead of raising.
    parsed = pl.lit(None, dtype=pl.Date)
    for fmt in formats:
        parsed = pl.coalesce(
            parsed, trimmed.str.strptime(pl.Date, format=fmt, strict=False)
        )
    return trimmed, parsed


def _select_date_formats(values: pl.Series, dayfirst: bool) -> tuple[list[str], int]:
    """Choose one format per ambiguous pair using evidence from the whole column.

    The format that parses the most non-null cells wins; ``dayfirst`` breaks
    ties. Cells that both formats in a pair accept are counted as ambiguous.
    """
    selected: list[str] = []
    ambiguous = 0

    for day_fmt, month_fmt in AMBIGUOUS_DATE_FORMATS:
        by_day = values.str.strptime(
            pl.Date, format=day_fmt, strict=False
        ).is_not_null()
        by_month = values.str.strptime(
            pl.Date, format=month_fmt, strict=False
        ).is_not_null()
        day_count = int(by_day.sum())
        month_count = int(by_month.sum())
        ambiguous += int((by_day & by_month).sum())

        if day_count > month_count or (day_count == month_count and dayfirst):
            selected.append(day_fmt)
        else:
            selected.append(month_fmt)

    return selected, ambiguous


def normalize_dates(
    frame: pl.DataFrame, columns: list[str], dayfirst: bool = True
) -> tuple[pl.DataFrame, int, int, int, list[str]]:
    """Normalise date columns to ``YYYY-MM-DD``.

    One format is selected per ambiguous numeric pair from the whole column,
    so a column never mixes day-first and month-first readings. Structurally
    unambiguous formats (ISO, month names, ...) remain available per cell.
    Values that cannot be parsed are preserved verbatim and counted in
    ``unparseable`` rather than being dropped or causing an exception.
    """
    ignored: list[str] = []
    normalized = 0
    unparseable = 0
    ambiguous = 0

    for column in columns:
        if column not in frame.columns:
            ignored.append(column)
            continue

        # Prepare trimmed values once so format selection sees the same text
        # that parsing will use.
        frame = frame.with_columns(
            pl.col(column).cast(pl.String).str.strip_chars().alias(_TRIM_COL)
        ).with_columns(
            pl.when(pl.col(_TRIM_COL) == "")
            .then(None)
            .otherwise(pl.col(_TRIM_COL))
            .alias(_TRIM_COL)
        )
        selected, column_ambiguous = _select_date_formats(frame[_TRIM_COL], dayfirst)
        ambiguous += column_ambiguous

        trimmed, parsed = _date_exprs(
            pl.col(column), (*UNAMBIGUOUS_DATE_FORMATS, *selected)
        )
        frame = frame.with_columns(
            trimmed.alias(_TRIM_COL),
            parsed.alias(_PARSED_COL),
        )
        present = frame[_TRIM_COL].is_not_null()
        parsed_ok = frame[_PARSED_COL].is_not_null()
        normalized += int((present & parsed_ok).sum())
        unparseable += int((present & ~parsed_ok).sum())

        frame = frame.with_columns(
            pl.coalesce(
                pl.col(_PARSED_COL).dt.strftime("%Y-%m-%d"),
                pl.col(_TRIM_COL),
            ).alias(column)
        ).drop(_TRIM_COL, _PARSED_COL)

    return frame, normalized, unparseable, ambiguous, ignored


def dedupe(
    frame: pl.DataFrame, column: str | None, ignore_case: bool = False
) -> tuple[pl.DataFrame, int, int, list[str]]:
    """Keep the first row per trimmed key, preserving case unless asked not to.

    Null and blank/whitespace-only keys are all kept: they are reported as
    missing rather than collapsed into one duplicate. Returns the reduced
    frame, the number of rows removed, the number of rows with a missing key,
    and any unknown column names.
    """
    if column is None:
        return frame, 0, 0, []
    if column not in frame.columns:
        return frame, 0, 0, [column]

    key = pl.col(column).cast(pl.String).str.strip_chars()
    if ignore_case:
        key = key.str.to_lowercase()
    key = pl.when(key == "").then(None).otherwise(key)

    missing_key = int(frame.select(key.is_null().sum()).item() or 0)
    before = frame.height

    reduced = (
        frame.with_columns(key.alias(_KEY_COL))
        .with_row_index(_ORDER_COL)
        .filter(pl.col(_KEY_COL).is_null() | pl.col(_KEY_COL).is_first_distinct())
        .sort(_ORDER_COL)
        .drop(_KEY_COL, _ORDER_COL)
    )
    return reduced, before - reduced.height, missing_key, []


# --------------------------------------------------------------------------- #
# Orchestration / rendering
# --------------------------------------------------------------------------- #
def clean_csv_bytes(data: bytes, options: CleanOptions) -> CleaningOutcome:
    """Apply all requested transforms and build the canonical summary.

    ``rows_in`` comes from the raw input records, not from the frame. The
    summary reconciles as ``rows_in == rows_out + rows_dropped +
    len(quarantined)``: malformed and blank rows count in ``rows_in`` but never
    in ``rows_out``, so both are accounted for in ``rows_dropped``.
    """
    t0 = time.perf_counter()
    result = read_csv_with_report(data, options.delimiter)
    del data

    rows_in = result.rows_in
    blank_rows_skipped = result.blank_rows_skipped
    quarantined = result.quarantined
    frame = result.frame
    well_formed_rows = frame.height
    ignored: list[str] = []

    frame, emails_changed, ignored_emails = normalize_emails(
        frame, options.email_columns
    )
    ignored.extend(ignored_emails)

    frame, dates_normalized, unparseable, ambiguous, ignored_dates = normalize_dates(
        frame, options.date_columns, dayfirst=options.date_dayfirst
    )
    ignored.extend(ignored_dates)

    frame, duplicates_removed, missing_keys, ignored_dedupe = dedupe(
        frame, options.dedupe_by, ignore_case=options.dedupe_ignore_case
    )
    ignored.extend(ignored_dedupe)

    fixes = {
        "emails_normalized": emails_changed,
        "dates_normalized": dates_normalized,
        "unparseable_dates": unparseable,
        "duplicates_removed": duplicates_removed,
    }
    if ambiguous:
        fixes["ambiguous_dates"] = ambiguous
    if missing_keys:
        fixes["rows_missing_dedupe_key"] = missing_keys

    # Data-quality warnings live in their own stable structure, with the
    # ignored column *names* preserved (item #18).
    warnings = WarningSummary(
        malformed_rows=len(quarantined),
        unknown_columns=list(ignored),
    )

    summary = SummaryReport(
        rows_in=rows_in,
        rows_out=frame.height,
        rows_dropped=(well_formed_rows - frame.height) + blank_rows_skipped,
        blank_rows_skipped=blank_rows_skipped,
        fixes_applied=fixes,
        warnings=warnings,
        processing_time_ms=round((time.perf_counter() - t0) * 1000, 2),
    )
    return CleaningOutcome(
        frame=frame,
        summary=summary,
        ignored_columns=ignored,
        quarantined=quarantined,
    )


_FORMULA_PREFIX_RE = r"^[=+\-@]"


def render_csv(frame: pl.DataFrame, escape_formulas: bool = True) -> bytes:
    """Serialise a cleaned frame back to UTF-8 CSV bytes.

    Cells starting with ``=``, ``+``, ``-`` or ``@`` are prefixed with a single
    quote so spreadsheets treat them as text. This affects CSV output only;
    the JSON records returned by the API stay unchanged (item #20).
    """
    if escape_formulas and frame.width:
        expressions = []
        for column in frame.columns:
            value = pl.col(column).cast(pl.String)
            risky = value.str.contains(_FORMULA_PREFIX_RE)
            expressions.append(
                pl.when(risky)
                .then(pl.lit("'") + value)
                .otherwise(value)
                .alias(column)
            )
        frame = frame.select(expressions)
    return frame.write_csv().encode("utf-8")


def render_quarantined_csv(quarantined: list[QuarantinedRecord]) -> bytes:
    """Render quarantined rows as a UTF-8 CSV companion for ZIP downloads."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["row_index", "record", "errors"])
    for row in quarantined:
        writer.writerow(
            [
                row.row_index,
                json.dumps(row.record, ensure_ascii=False),
                json.dumps(row.errors, ensure_ascii=False),
            ]
        )
    return buffer.getvalue().encode("utf-8")
