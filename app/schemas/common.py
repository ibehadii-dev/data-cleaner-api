"""Models shared across endpoints."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Base model that rejects unknown fields so payload typos surface as 422s."""

    model_config = ConfigDict(extra="forbid")


class WarningSummary(StrictModel):
    """Non-fatal data-quality warnings shared by both endpoints."""

    malformed_rows: int = 0
    unknown_columns: list[str] = []


class SummaryReport(StrictModel):
    rows_in: int
    rows_out: int
    rows_dropped: int
    blank_rows_skipped: int = 0
    fixes_applied: dict[str, int] = {}
    warnings: WarningSummary = Field(default_factory=WarningSummary)


class QuarantinedRecord(StrictModel):
    row_index: int
    record: Any  # raw row (list for CSV, object/value for JSON)
    errors: list[str]


class ErrorResponse(StrictModel):
    detail: str
