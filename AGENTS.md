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

## Architecture Decision Records
- Read [`docs/adr/README.md`](docs/adr/README.md) before modifying schemas, reconciliation rules, validation, or the cleaning pipeline. In particular, ADR-0002 (row reconciliation and quarantines), ADR-0004 (validation hardening and formula escaping), ADR-0005 (deduplication, date, phone and company normalization), and ADR-0006 (presets, exports and telemetry) record the decisions those changes must preserve.