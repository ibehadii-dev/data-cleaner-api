"""Pure validation logic for JSON record batches."""

from __future__ import annotations

import math
import re
from typing import Any

from ..schemas.validate import (
    FieldRule,
    QuarantinedRecord,
    ValidateResponse,
    ValidateSummary,
)
from ..utils.dates import parse_date_string

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
BOOLEANS = {"true", "false", "yes", "no", "1", "0"}


def _check_type(value: Any, rule: FieldRule) -> str | None:
    """Return a human-readable problem description, or ``None`` when valid."""
    kind = rule.type

    if kind == "string":
        if not isinstance(value, str):
            return "must be a string"

    elif kind == "email":
        if not isinstance(value, str) or not EMAIL_RE.match(value.strip()):
            return "must be a valid email address"

    elif kind == "date":
        if parse_date_string(value) is None:
            return "must be a valid date"

    elif kind in ("number", "integer"):
        if isinstance(value, bool):
            return "must be a number"

        try:
            if isinstance(value, int) and not isinstance(value, bool):
                # Python ints are arbitrary precision; float(value) would raise
                # OverflowError for very large values such as 10**400.
                return None
            if isinstance(value, float):
                number = value
            elif isinstance(value, str):
                number = float(value.strip())
            else:
                return "must be a number"

            if not math.isfinite(number):
                return "must be a finite number"
            if kind == "integer" and not number.is_integer():
                return "must be an integer"
        except (ValueError, OverflowError, TypeError):
            return "must be a number"

    elif kind == "boolean":
        if isinstance(value, bool):
            return None
        if isinstance(value, str) and value.strip().lower() in BOOLEANS:
            return None
        return "must be a boolean"

    return None


def validate_records(
    records: list[dict[str, Any]], field_rules: dict[str, FieldRule]
) -> ValidateResponse:
    """Validate every record, quarantining invalid ones with all their errors."""
    valid: list[dict[str, Any]] = []
    quarantined: list[QuarantinedRecord] = []
    errors_by_field: dict[str, int] = {}

    for index, record in enumerate(records):
        errors: list[str] = []

        if not isinstance(record, dict):
            quarantined.append(
                QuarantinedRecord(
                    row_index=index,
                    record=record,
                    errors=["record must be a JSON object"],
                )
            )
            errors_by_field["<record>"] = errors_by_field.get("<record>", 0) + 1
            continue

        for field, rule in field_rules.items():
            value = record.get(field)
            is_empty = value is None or (
                isinstance(value, str) and not value.strip()
            )

            if is_empty:
                if rule.required:
                    errors.append(f"'{field}' is required")
                    errors_by_field[field] = errors_by_field.get(field, 0) + 1
                # Optional / nullable empty values are simply skipped.
                continue

            problem = _check_type(value, rule)
            if problem:
                errors.append(f"'{field}' {problem}")
                errors_by_field[field] = errors_by_field.get(field, 0) + 1

        if errors:
            quarantined.append(
                QuarantinedRecord(row_index=index, record=record, errors=errors)
            )
        else:
            valid.append(record)

    total = len(records)
    summary = ValidateSummary(
        rows_in=total,
        rows_out=len(valid),
        rows_dropped=len(quarantined),
        fixes_applied={},
        valid_count=len(valid),
        quarantined_count=len(quarantined),
        errors_by_field=errors_by_field,
    )
    return ValidateResponse(
        valid=valid, quarantined=quarantined, summary=summary
    )
