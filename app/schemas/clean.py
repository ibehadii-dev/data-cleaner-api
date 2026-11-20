"""Schemas for the CSV cleaning endpoint."""

from typing import Any, Literal

from pydantic import field_validator

from ..utils.params import normalize_list_field
from .common import StrictModel, SummaryReport


class CleanOptions(StrictModel):
    dedupe_by: str | None = None
    date_columns: list[str] = []
    email_columns: list[str] = []
    output_format: Literal["json", "csv"] = "json"

    @field_validator("dedupe_by", mode="before")
    @classmethod
    def blank_is_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("date_columns", "email_columns", mode="before")
    @classmethod
    def coerce_list(cls, value: Any) -> list[str]:
        return normalize_list_field(value) or []


class CleanJsonResponse(StrictModel):
    data: list[dict[str, Any]]
    summary: SummaryReport
