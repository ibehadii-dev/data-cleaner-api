"""Pure Polars-based CSV cleaning transforms.

Nothing in this module knows about HTTP; all functions take bytes/DataFrames
and return DataFrames plus plain counters.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

import polars as pl

from ..schemas.clean import CleanOptions
from ..schemas.common import QuarantinedRecord, SummaryReport
from ..utils.dates import DATE_FORMATS
from ..utils.errors import CsvReadError

MAX_FILE_BYTES = 25 * 1024 * 1024
_TRIM_COL = "__clean_date_trimmed__"
_PARSED_COL = "__clean_date_parsed__"
_KEY_COL = "__dedupe_key__"
_ORDER_COL = "__dedupe_order__"


@dataclass
class CleaningOutcome:
    frame: pl.DataFrame
    summary: SummaryReport
    ignored_columns: list[str]
    quarantined: list[QuarantinedRecord]


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
def decode_csv_bytes(data: bytes) -> str:
    """Decode uploaded bytes to text, raising CsvReadError on bad input.

    ``utf-8-sig`` strips an optional BOM; Latin-1 is the legacy fallback.
    Empty / BOM-only files return an empty string. Upload size limiting is
    the router's job: it rejects oversized files before buffering them.
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
            # Latin-1 maps every byte to a valid code point, so this is our
            # last-resort decoding step for legacy exports.
            text = data.decode("latin-1")
        except UnicodeDecodeError as exc:  # defensive: practically unreachable
            raise CsvReadError(
                "Unreadable CSV file: could not decode as UTF-8 or Latin-1."
            ) from exc

    if "\x00" in text:
        raise CsvReadError("Unreadable CSV file: binary content detected.")
    if not text.strip():
        return ""
    return text


def _filter_malformed_rows(text: str) -> tuple[str, list[QuarantinedRecord]]:
    """Split ragged CSV rows out; return clean text and quarantine records.

    Rows whose field count differs from the header are removed from the text
    handed to Polars and reported with a reason. Blank rows are kept because
    Polars turns them into all-null records.
    """
    try:
        rows = list(csv.reader(io.StringIO(text, newline="")))
    except csv.Error as exc:
        raise CsvReadError(f"Unreadable CSV file: {exc}") from exc

    if not rows or not rows[0]:
        return "", []

    header, data_rows = rows[0], rows[1:]
    expected = len(header)
    kept: list[list[str]] = []
    quarantined: list[QuarantinedRecord] = []

    for index, row in enumerate(data_rows):
        if not row or len(row) == expected:
            kept.append(row)
            continue
        quarantined.append(
            QuarantinedRecord(
                row_index=index,
                record=row,
                errors=[
                    f"malformed row: expected {expected} columns, found {len(row)}"
                ],
            )
        )

    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(kept)
    return buffer.getvalue(), quarantined


def read_csv_bytes_with_quarantine(
    data: bytes,
) -> tuple[pl.DataFrame, list[QuarantinedRecord]]:
    """Parse CSV bytes, separating malformed rows from the Polars frame."""
    text = decode_csv_bytes(data)
    if not text:
        return pl.DataFrame(), []

    filtered_text, quarantined = _filter_malformed_rows(text)
    if not filtered_text:
        return pl.DataFrame(), quarantined

    try:
        frame = pl.read_csv(
            io.BytesIO(filtered_text.encode("utf-8")),
            infer_schema_length=0,  # read every column as String
            truncate_ragged_lines=True,  # defensive; ragged rows filtered above
        )
    except Exception as exc:  # Polars raises several distinct exception types
        raise CsvReadError(f"Unreadable CSV file: {exc}") from exc
    return frame, quarantined


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


def _date_exprs(expression: pl.Expr) -> tuple[pl.Expr, pl.Expr]:
    """Build the trimmed-string and parsed-Date expressions for a column."""
    trimmed = expression.cast(pl.String).str.strip_chars()
    trimmed = pl.when(trimmed == "").then(None).otherwise(trimmed)

    # strict=False makes an unparseable value null instead of raising.
    parsed = pl.lit(None, dtype=pl.Date)
    for fmt in DATE_FORMATS:
        parsed = pl.coalesce(
            parsed, trimmed.str.strptime(pl.Date, format=fmt, strict=False)
        )
    return trimmed, parsed


def normalize_dates(
    frame: pl.DataFrame, columns: list[str]
) -> tuple[pl.DataFrame, int, int, list[str]]:
    """Normalise date columns to ``YYYY-MM-DD``.

    Values that cannot be parsed are preserved verbatim and counted in
    ``unparseable`` rather than being dropped or causing an exception.
    """
    ignored: list[str] = []
    normalized = 0
    unparseable = 0

    for column in columns:
        if column not in frame.columns:
            ignored.append(column)
            continue

        trimmed, parsed = _date_exprs(pl.col(column))
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

    return frame, normalized, unparseable, ignored


def dedupe(
    frame: pl.DataFrame, column: str | None
) -> tuple[pl.DataFrame, int, int, list[str]]:
    """Keep the first row per case/trim-insensitive key.

    Returns the reduced frame, the number of rows removed, the number of rows
    with a missing key, and any unknown column names.
    """
    if column is None:
        return frame, 0, 0, []
    if column not in frame.columns:
        return frame, 0, 0, [column]

    key = frame[column].cast(pl.String).str.strip_chars().str.to_lowercase()
    missing_key = int(key.is_null().sum())
    before = frame.height

    reduced = (
        frame.with_columns(key.alias(_KEY_COL))
        .with_row_index(_ORDER_COL)
        .unique(subset=[_KEY_COL], keep="first", maintain_order=True)
        .sort(_ORDER_COL)
        .drop(_KEY_COL, _ORDER_COL)
    )
    return reduced, before - reduced.height, missing_key, []


# --------------------------------------------------------------------------- #
# Orchestration / rendering
# --------------------------------------------------------------------------- #
def clean_csv_bytes(data: bytes, options: CleanOptions) -> CleaningOutcome:
    """Apply all requested transforms and build the canonical summary.

    The summary reconciles as ``rows_in == rows_out + rows_dropped
    + len(quarantined)``: malformed rows count in ``rows_in`` but never in
    ``rows_out``, and ``rows_dropped`` covers dedupe removals only.
    """
    frame, quarantined = read_csv_bytes_with_quarantine(data)
    rows_in = frame.height + len(quarantined)
    well_formed_rows = frame.height
    ignored: list[str] = []

    frame, emails_changed, ignored_emails = normalize_emails(
        frame, options.email_columns
    )
    ignored.extend(ignored_emails)

    frame, dates_normalized, unparseable, ignored_dates = normalize_dates(
        frame, options.date_columns
    )
    ignored.extend(ignored_dates)

    frame, duplicates_removed, missing_keys, ignored_dedupe = dedupe(
        frame, options.dedupe_by
    )
    ignored.extend(ignored_dedupe)

    fixes = {
        "emails_normalized": emails_changed,
        "dates_normalized": dates_normalized,
        "unparseable_dates": unparseable,
        "duplicates_removed": duplicates_removed,
        "malformed_rows": len(quarantined),
    }
    if missing_keys:
        fixes["rows_missing_dedupe_key"] = missing_keys
    if ignored:
        fixes["unknown_columns_ignored"] = len(ignored)

    summary = SummaryReport(
        rows_in=rows_in,
        rows_out=frame.height,
        rows_dropped=well_formed_rows - frame.height,
        fixes_applied=fixes,
    )
    return CleaningOutcome(
        frame=frame,
        summary=summary,
        ignored_columns=ignored,
        quarantined=quarantined,
    )


def render_csv(frame: pl.DataFrame) -> bytes:
    """Serialise a cleaned frame back to UTF-8 CSV bytes."""
    return frame.write_csv().encode("utf-8")
