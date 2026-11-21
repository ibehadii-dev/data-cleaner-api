"""Models shared across endpoints."""

from typing import Any

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Base model that rejects unknown fields so payload typos surface as 422s."""

    model_config = ConfigDict(extra="forbid")


class SummaryReport(StrictModel):
    rows_in: int
    rows_out: int
    rows_dropped: int
    blank_rows_skipped: int = 0
    fixes_applied: dict[str, int] = {}


class QuarantinedRecord(StrictModel):
    row_index: int
    record: Any  # raw row (list for CSV, object/value for JSON)
    errors: list[str]


class ErrorResponse(StrictModel):
    detail: str
