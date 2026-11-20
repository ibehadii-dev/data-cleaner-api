"""Schemas for the JSON validation endpoint."""

from typing import Any, Literal

from .common import QuarantinedRecord, StrictModel


class FieldRule(StrictModel):
    required: bool = False
    type: Literal["string", "email", "date", "number", "integer", "boolean"] = "string"
    nullable: bool = False


class ValidateRequest(StrictModel):
    records: list[dict[str, Any]]
    field_rules: dict[str, FieldRule]


class ValidateSummary(StrictModel):
    rows_in: int
    rows_out: int
    rows_dropped: int
    fixes_applied: dict[str, int] = {}
    valid_count: int
    quarantined_count: int
    errors_by_field: dict[str, int] = {}


class ValidateResponse(StrictModel):
    valid: list[dict[str, Any]]
    quarantined: list[QuarantinedRecord]
    summary: ValidateSummary
