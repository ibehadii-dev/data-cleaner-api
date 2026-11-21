"""Shared date-format handling for both the CSV cleaner and JSON validator."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

# Ordered from most to least specific. Ambiguous numeric dates such as
# ``03/04/2024`` are interpreted as day-first (3 April 2024).
DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%m-%d-%Y",
    "%d %b %Y",
    "%b %d, %Y",
    "%d %B %Y",
    "%B %d, %Y",
    "%Y%m%d",
)

# Numeric formats where both readings can be valid (e.g. 03/04/2024). The CSV
# cleaner picks ONE format from each pair for an entire column, so a column
# never mixes day-first and month-first interpretations.
AMBIGUOUS_DATE_FORMATS: tuple[tuple[str, str], ...] = (
    ("%d/%m/%Y", "%m/%d/%Y"),
    ("%d-%m-%Y", "%m-%d-%Y"),
)

_AMBIGUOUS_SET = {fmt for pair in AMBIGUOUS_DATE_FORMATS for fmt in pair}

# All other formats are structurally unambiguous and stay available per cell.
UNAMBIGUOUS_DATE_FORMATS: tuple[str, ...] = tuple(
    fmt for fmt in DATE_FORMATS if fmt not in _AMBIGUOUS_SET
)


def parse_date_string(value: Any) -> date | None:
    """Best-effort conversion of a JSON value to a ``date``.

    Returns ``None`` for anything that cannot be interpreted as a date;
    callers decide whether that is an error or merely a value to preserve.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    # Only the shared DATE_FORMATS are accepted. This deliberately excludes
    # ISO-8601 offsets / fractional seconds that the CSV cleaner cannot parse,
    # so /validate/json and /clean/csv agree on what counts as a date.
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None
