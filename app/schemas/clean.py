"""Schemas for the CSV cleaning endpoint."""

from typing import Any, Literal

from pydantic import Field, field_validator

from ..utils.params import normalize_list_field
from .common import QuarantinedRecord, StrictModel, SummaryReport


_ALLOWED_DELIMITERS = (",", ";", "\t", "|")


class CleanOptions(StrictModel):
    dedupe_by: str | None = None
    dedupe_ignore_case: bool = False
    date_columns: list[str] = []
    date_dayfirst: bool = True
    email_columns: list[str] = []
    phone_columns: list[str] = Field(default_factory=list)
    default_country_code: str = Field(default="US", min_length=2, max_length=2)
    preset: Literal["hubspot", "salesforce"] | None = None
    output_format: Literal["json", "csv", "zip"] = "json"
    delimiter: str | None = None
    include_quarantined: bool = False

    @field_validator("delimiter", mode="before")
    @classmethod
    def normalize_delimiter(cls, value: Any) -> str | None:
        """Accept one of ``, ; tab |``; allow a literal ``\\t`` from forms."""
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("delimiter must be one of ',', ';', tab or '|'")
        if value in _ALLOWED_DELIMITERS:
            return value
        if value == "\\t":
            return "\t"
        text = value.strip()
        if not text:
            return None
        if text == "\\t":
            return "\t"
        if text in _ALLOWED_DELIMITERS:
            return text
        raise ValueError("delimiter must be one of ',', ';', tab or '|'")

    @field_validator("dedupe_by", mode="before")
    @classmethod
    def blank_is_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("date_columns", "email_columns", "phone_columns", mode="before")
    @classmethod
    def coerce_list(cls, value: Any) -> list[str]:
        return normalize_list_field(value) or []

    @field_validator("default_country_code", mode="before")
    @classmethod
    def uppercase_country_code(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().upper()
        return value


class CleanJsonResponse(StrictModel):
    data: list[dict[str, Any]]
    summary: SummaryReport
    quarantined: list[QuarantinedRecord] = []
