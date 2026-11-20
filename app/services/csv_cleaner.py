"""Pure Polars-based CSV cleaning transforms.

Nothing in this module knows about HTTP; all functions take bytes/DataFrames
and return DataFrames plus plain counters.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import polars as pl

from ..schemas.clean import CleanOptions
from ..schemas.common import SummaryReport
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


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
def read_csv_bytes(data: bytes) -> pl.DataFrame:
    """Decode and parse uploaded CSV bytes without ever crashing the caller.

    * ``utf-8-sig`` transparently strips a UTF-8 BOM (and accepts no-BOM input).
    * Latin-1 is used as a fallback for legacy exports.
    * Empty / BOM-only files produce an empty DataFrame.
    * Binary content and Polars parse failures raise :class:`CsvReadError`.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise CsvReadError("Uploaded file must be a binary file.")
    data = bytes(data)

    if len(data) > MAX_FILE_BYTES:
        raise CsvReadError("Uploaded CSV exceeds the 25 MB size limit.")

    if not data:
        return pl.DataFrame()

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
        return pl.DataFrame()

    try:
        return pl.read_csv(
            io.BytesIO(text.encode("utf-8")),
            infer_schema_length=0,  # read every column as String
            truncate_ragged_lines=True,  # never crash on malformed rows
        )
    except Exception as exc:  # Polars raises several distinct exception types
        raise CsvReadError(f"Unreadable CSV file: {exc}") from exc


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
    """Apply all requested transforms and build the canonical summary."""
    frame = read_csv_bytes(data)
    rows_in = frame.height
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
    }
    if missing_keys:
        fixes["rows_missing_dedupe_key"] = missing_keys
    if ignored:
        fixes["unknown_columns_ignored"] = len(ignored)

    summary = SummaryReport(
        rows_in=rows_in,
        rows_out=frame.height,
        rows_dropped=rows_in - frame.height,
        fixes_applied=fixes,
    )
    return CleaningOutcome(frame=frame, summary=summary, ignored_columns=ignored)


def render_csv(frame: pl.DataFrame) -> bytes:
    """Serialise a cleaned frame back to UTF-8 CSV bytes."""
    return frame.write_csv().encode("utf-8")
