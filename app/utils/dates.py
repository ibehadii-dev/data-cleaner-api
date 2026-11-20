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

    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    # Final fallback for ISO-8601 strings with offsets / fractional seconds.
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None
