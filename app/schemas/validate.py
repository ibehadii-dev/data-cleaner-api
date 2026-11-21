"""Schemas for the JSON validation endpoint."""

from typing import Annotated, Any, Literal

from pydantic import Field

from .common import QuarantinedRecord, StrictModel, WarningSummary


class FieldRule(StrictModel):
    required: bool = False
    type: Literal["string", "email", "date", "number", "integer", "boolean"] = "string"
    nullable: bool = False


MAX_RECORDS_PER_BATCH = 1000


class ValidateRequest(StrictModel):
    # Cap the number of records so one request cannot monopolise the worker.
    records: Annotated[
        list[dict[str, Any]], Field(max_length=MAX_RECORDS_PER_BATCH)
    ]
    field_rules: dict[str, FieldRule]


class ValidateSummary(StrictModel):
    rows_in: int
    rows_out: int
    rows_dropped: int
    fixes_applied: dict[str, int] = {}
    valid_count: int
    quarantined_count: int
    errors_by_field: dict[str, int] = {}
    warnings: WarningSummary = Field(default_factory=WarningSummary)


class ValidateResponse(StrictModel):
    valid: list[dict[str, Any]]
    quarantined: list[QuarantinedRecord]
    summary: ValidateSummary
