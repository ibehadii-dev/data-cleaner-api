"""Models shared across endpoints."""

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Base model that rejects unknown fields so payload typos surface as 422s."""

    model_config = ConfigDict(extra="forbid")


class SummaryReport(StrictModel):
    rows_in: int
    rows_out: int
    rows_dropped: int
    fixes_applied: dict[str, int] = {}


class ErrorResponse(StrictModel):
    detail: str
