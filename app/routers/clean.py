"""POST /clean/csv — multipart CSV upload with cleaning options."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError

from ..schemas.clean import CleanJsonResponse, CleanOptions
from ..schemas.common import ErrorResponse
from ..services.csv_cleaner import clean_csv_bytes, render_csv

router = APIRouter(prefix="/clean", tags=["clean"])


def _pick(
    name: str,
    form_value: Any,
    query: Any,
) -> Any:
    """Prefer a form value; fall back to the same key in the query string."""
    if form_value not in (None, "", []):
        return form_value
    return query.get(name)


def _resolve_options(
    request: Request,
    dedupe_by: str | None,
    date_columns: list[str] | None,
    email_columns: list[str] | None,
    output_format: str | None,
) -> CleanOptions:
    query = request.query_params
    try:
        return CleanOptions(
            dedupe_by=_pick("dedupe_by", dedupe_by, query),
            date_columns=_pick("date_columns", date_columns, query),
            email_columns=_pick("email_columns", email_columns, query),
            output_format=_pick("output_format", output_format, query) or "json",
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail=exc.errors(include_url=False)
        ) from exc


@router.post(
    "/csv",
    response_model=CleanJsonResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Unreadable CSV file"},
        422: {"description": "Invalid option value or missing file part"},
    },
)
async def clean_csv(
    request: Request,
    file: UploadFile = File(...),
    dedupe_by: Annotated[str | None, Form()] = None,
    date_columns: Annotated[list[str] | None, Form()] = None,
    email_columns: Annotated[list[str] | None, Form()] = None,
    output_format: Annotated[str | None, Form()] = None,
) -> Any:
    options = _resolve_options(
        request, dedupe_by, date_columns, email_columns, output_format
    )
    outcome = clean_csv_bytes(await file.read(), options)

    if options.output_format == "csv":
        summary = outcome.summary
        headers = {
            "X-Rows-In": str(summary.rows_in),
            "X-Rows-Out": str(summary.rows_out),
            "X-Rows-Dropped": str(summary.rows_dropped),
            "X-Fixes-Applied": json.dumps(summary.fixes_applied, sort_keys=True),
            "X-Unknown-Columns-Ignored": json.dumps(outcome.ignored_columns),
            "Content-Disposition": 'attachment; filename="cleaned.csv"',
        }
        return Response(
            content=render_csv(outcome.frame),
            media_type="text/csv",
            headers=headers,
        )

    return CleanJsonResponse(
        data=outcome.frame.to_dicts(), summary=outcome.summary
    )
