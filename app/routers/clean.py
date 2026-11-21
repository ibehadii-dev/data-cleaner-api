"""POST /clean/csv — multipart CSV upload with cleaning options."""

from __future__ import annotations

import io
import json
import zipfile
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import ValidationError

from ..schemas.clean import CleanJsonResponse, CleanOptions
from ..schemas.common import ErrorResponse
from ..services import csv_cleaner
from ..services.csv_cleaner import (
    clean_csv_bytes,
    render_csv,
    render_quarantined_csv,
)

router = APIRouter(prefix="/clean", tags=["clean"])

UPLOAD_CHUNK_BYTES = 64 * 1024


def _pick(
    name: str,
    form_value: Any,
    query: Any,
) -> Any:
    """Prefer a form value; fall back to the same key in the query string."""
    if form_value not in (None, "", []):
        return form_value
    return query.get(name)


def _pick_list(
    name: str,
    form_value: list[str] | None,
    query: Any,
) -> list[str] | None:
    """Like :func:`_pick`, but keep *every* repeated query parameter (item #13)."""
    if form_value not in (None, "", []):
        return form_value
    values = query.getlist(name)
    return values or None


def _resolve_options(
    request: Request,
    dedupe_by: str | None,
    dedupe_ignore_case: str | None,
    date_columns: list[str] | None,
    date_dayfirst: str | None,
    email_columns: list[str] | None,
    output_format: str | None,
    delimiter: str | None,
    include_quarantined: str | None,
) -> CleanOptions:
    query = request.query_params
    ignore_case = _pick("dedupe_ignore_case", dedupe_ignore_case, query)
    dayfirst = _pick("date_dayfirst", date_dayfirst, query)
    quarantine = _pick("include_quarantined", include_quarantined, query)
    try:
        return CleanOptions(
            dedupe_by=_pick("dedupe_by", dedupe_by, query),
            dedupe_ignore_case=False if ignore_case is None else ignore_case,
            date_columns=_pick_list("date_columns", date_columns, query),
            date_dayfirst=True if dayfirst is None else dayfirst,
            email_columns=_pick_list("email_columns", email_columns, query),
            output_format=_pick("output_format", output_format, query) or "json",
            delimiter=_pick("delimiter", delimiter, query),
            include_quarantined=False if quarantine is None else quarantine,
        )
    except ValidationError as exc:
        # Context values can hold raw exception objects (e.g. our delimiter
        # validator), which JSONResponse cannot serialise.
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_url=False, include_context=False),
        ) from exc


async def read_upload_limited(file: UploadFile) -> bytes:
    """Read an upload in chunks, enforcing a cumulative size cap.

    Starlette has usually already parsed and spooled the multipart body before
    this handler runs, so this guard protects *handler* memory (it stops a
    huge spooled file from being copied into a bytes object). The advertised
    network-level limit belongs at the proxy / Content-Length middleware.
    """
    limit = csv_cleaner.MAX_FILE_BYTES
    if file.size is not None and file.size > limit:
        raise HTTPException(
            status_code=413,
            detail=(
                "Uploaded CSV exceeds the "
                f"{limit // (1024 * 1024)} MB size limit."
            ),
        )

    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(UPLOAD_CHUNK_BYTES):
        total += len(chunk)
        if total > csv_cleaner.MAX_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    "Uploaded CSV exceeds the "
                    f"{csv_cleaner.MAX_FILE_BYTES // (1024 * 1024)} MB size limit."
                ),
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _build_quarantine_zip(cleaned: bytes, quarantined: bytes) -> bytes:
    """Bundle the cleaned data and its quarantine report into one download."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("cleaned.csv", cleaned)
        archive.writestr("quarantined.csv", quarantined)
    return buffer.getvalue()


@router.post(
    "/csv",
    response_model=CleanJsonResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Unreadable CSV file"},
        413: {"model": ErrorResponse, "description": "Upload exceeds the size limit"},
        422: {"description": "Invalid option value or missing file part"},
    },
)
async def clean_csv(
    request: Request,
    file: UploadFile = File(...),
    dedupe_by: Annotated[str | None, Form()] = None,
    dedupe_ignore_case: Annotated[str | None, Form()] = None,
    date_columns: Annotated[list[str] | None, Form()] = None,
    date_dayfirst: Annotated[str | None, Form()] = None,
    email_columns: Annotated[list[str] | None, Form()] = None,
    output_format: Annotated[str | None, Form()] = None,
    delimiter: Annotated[str | None, Form()] = None,
    include_quarantined: Annotated[str | None, Form()] = None,
) -> Any:
    options = _resolve_options(
        request,
        dedupe_by,
        dedupe_ignore_case,
        date_columns,
        date_dayfirst,
        email_columns,
        output_format,
        delimiter,
        include_quarantined,
    )
    data = await read_upload_limited(file)
    outcome = await run_in_threadpool(clean_csv_bytes, data, options)
    del data  # the frame and summary are all that remain of the upload

    if options.output_format == "csv":
        summary = outcome.summary
        headers = {
            "X-Rows-In": str(summary.rows_in),
            "X-Rows-Out": str(summary.rows_out),
            "X-Rows-Dropped": str(summary.rows_dropped),
            "X-Blank-Rows-Skipped": str(summary.blank_rows_skipped),
            "X-Quarantined-Rows": str(len(outcome.quarantined)),
            "X-Fixes-Applied": json.dumps(summary.fixes_applied, sort_keys=True),
            "X-Unknown-Columns-Ignored": json.dumps(outcome.ignored_columns),
            "X-Warnings": json.dumps(
                summary.warnings.model_dump(), sort_keys=True
            ),
            "Content-Disposition": 'attachment; filename="cleaned.csv"',
        }
        cleaned = await run_in_threadpool(render_csv, outcome.frame)
        if options.include_quarantined:
            details = await run_in_threadpool(
                render_quarantined_csv, outcome.quarantined
            )
            content = await run_in_threadpool(
                _build_quarantine_zip, cleaned, details
            )
            zip_headers = dict(headers)
            zip_headers["Content-Disposition"] = 'attachment; filename="cleaned.zip"'
            return Response(
                content=content,
                media_type="application/zip",
                headers=zip_headers,
            )
        return Response(
            content=cleaned,
            media_type="text/csv",
            headers=headers,
        )

    # Stream Polars' own row-oriented JSON straight into the response body.
    # Building to_dicts() plus a second Pydantic list would double peak memory.
    records_json = await run_in_threadpool(outcome.frame.write_json)
    summary_json = outcome.summary.model_dump_json()
    quarantined_json = json.dumps(
        [row.model_dump(mode="json") for row in outcome.quarantined],
        ensure_ascii=False,
    )
    body = (
        '{"data":'
        + records_json
        + ',"summary":'
        + summary_json
        + ',"quarantined":'
        + quarantined_json
        + "}"
    )
    return Response(content=body, media_type="application/json")
