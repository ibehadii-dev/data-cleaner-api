# AGENTS.md

## Project Overview
FastAPI microservice for cleaning messy CSV exports and validating JSON payloads for RevOps and data analysts.

## Tech Stack
- Python 3.11+
- FastAPI + Uvicorn
- Pydantic v2 (strict request/response models)
- Polars (CSV parsing and tabular transforms; do not use pandas)
- pytest + httpx (API and edge-case tests)

## Directory Layout
- `app/main.py` — FastAPI app, lifespan, and router registration
- `app/routers/` — Endpoint definitions (`clean.py`, `validate.py`)
- `app/services/` — Pure cleaning/validation functions (Polars transforms)
- `app/schemas/` — Pydantic v2 models
- `tests/` — Pytest suite mirroring `app/routers/` and `app/services/`

## Coding & Data Rules
- Always return both the cleaned data/records and a summary report (rows_in, rows_out, rows_dropped, fixes_applied).
- Handle UTF-8 (with or without BOM) and Latin-1 encoded CSVs gracefully; return a clear 400 error on unreadable files.
- Never silently drop malformed rows—either quarantine them with a reason or count them in the report.
- Every endpoint and transform function must have pytest coverage for empty inputs, missing values, and duplicate keys.

## Verification
- Run `pytest -v` after any code change and fix all failures before finishing.