"""E.164 phone normalization shared by the CSV cleaning pipeline."""

from __future__ import annotations

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat


def normalize_phone_e164(
    value: str | None, default_region: str = "US"
) -> tuple[str | None, bool, str | None]:
    """Return ``(e164, was_changed, error)`` for one phone value.

    Blank values are left alone (never quarantined). Valid numbers are
    formatted as E.164; invalid or unparseable numbers are reported with a
    human-readable error so the caller can quarantine the row.
    """
    if value is None:
        return None, False, None
    raw = value.strip() if isinstance(value, str) else str(value).strip()
    if not raw:
        return None, False, None

    try:
        parsed = phonenumbers.parse(raw, default_region)
    except NumberParseException:
        return (
            None,
            False,
            f"Invalid phone number for region {default_region}: {raw}",
        )

    if not phonenumbers.is_valid_number(parsed):
        return (
            None,
            False,
            f"Invalid phone number for region {default_region}: {raw}",
        )

    formatted = phonenumbers.format_number(parsed, PhoneNumberFormat.E164)
    return formatted, formatted != raw, None
