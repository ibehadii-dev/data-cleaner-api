"""POST /demo/messy-leads — run the canonical messy sample through the cleaner."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError

from ..schemas.clean import CleanJsonResponse, CleanOptions
from ..services.csv_cleaner import clean_csv_bytes
from .clean import build_clean_response

router = APIRouter(tags=["demo"])

DEMO_CSV_PATH = Path(__file__).resolve().parent.parent.parent / "test_messy_leads.csv"

# Used when the repository sample is not deployed (e.g. app/ only). Keep in sync
# with static/index.html's SAMPLE_CSV and with test_messy_leads.csv at the root.
FALLBACK_MESSY_LEADS_CSV = (
    "full_name,email,created_at,company\n"
    "  Ada Lovelace , ADA@Example.COM ,TBD,Analytical Engines Ltd\n"
    "Ada Lovelace,ada@example.com,15/03/2024,Analytical Engines Ltd\n"
    "Grace Hopper,GRACE@NAVY.MIL,2024-02-01,US Navy\n"
    "Alan Turing,,TBD,Bletchley Park\n"
    "Katherine Johnson,katherine@nasa.gov,03/04/2024, NASA ,extra-field\n"
    "\n"
    "\n"
)


def load_demo_csv_bytes() -> bytes:
    """Read the shipped sample, falling back to the inline one on any I/O error."""
    try:
        return DEMO_CSV_PATH.read_bytes()
    except OSError:
        return FALLBACK_MESSY_LEADS_CSV.encode("utf-8")


@router.post(
    "/demo/messy-leads",
    response_model=CleanJsonResponse,
    summary="Clean the bundled messy-leads sample",
    responses={
        200: {
            "description": (
                "Cleaned leads as JSON, CSV or a three-file ZIP bundle "
                "depending on output_format"
            ),
            "content": {"text/csv": {}, "application/zip": {}},
        },
        422: {"description": "Invalid output_format value"},
    },
)
async def demo_messy_leads(
    request: Request,
    output_format: Annotated[Literal["json", "csv", "zip"] | None, Form()] = None,
) -> Any:
    """Clean the demo leads with the canonical RevOps option set.

    ``output_format`` may be sent as a form field or a query parameter; the
    response is produced by the same helper as ``POST /clean/csv``.
    """
    requested = request.query_params.get("output_format") or output_format or "json"
    try:
        options = CleanOptions(
            dedupe_by="email",
            dedupe_ignore_case=True,
            date_columns=["created_at"],
            email_columns=["email"],
            output_format=requested,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(include_url=False, include_context=False),
        ) from exc

    outcome = await run_in_threadpool(clean_csv_bytes, load_demo_csv_bytes(), options)
    return await build_clean_response(outcome, options.output_format)
