# ADR-0001: FastAPI + Polars + Pydantic v2 stack

- **Status:** Accepted
- **Date:** 2026-11-21

## Context

The service cleans messy CRM/export CSVs and validates JSON record batches for
RevOps and data analysts. It must be fast on multi-megabyte uploads, strictly
typed at the HTTP boundary, easy to test without a database, and deployable as a
single process with no queue or worker infrastructure. Earlier profiling showed
Python-side row materialisation and synchronous work on the event loop were the
main causes of high memory use and slow health checks.

## Decision

Use **FastAPI + Uvicorn** as the HTTP layer, **Polars** for all tabular parsing
and transforms, and **Pydantic v2** for every request/response schema.

- Four endpoints: `GET /health`, `POST /clean/csv`, `POST /validate/json`, and
  `POST /demo/messy-leads`. A single-file frontend is served at `/` and mounted
  at `/static`; there is no build step.
- **No pandas, no ORM, no database.** All cleaning logic lives in pure service
  functions (`app/services/csv_cleaner.py`, `app/services/json_validator.py`)
  that take bytes/records and return frames plus counters. Routers own only
  HTTP concerns: option parsing, size guards, and response rendering.
- All models derive from `StrictModel` with `model_config = ConfigDict(extra="forbid")`,
  so payload typos surface as HTTP 422 instead of being silently ignored.
- CPU-bound cleaning is offloaded with `await run_in_threadpool(...)`;
  `validate_json` is a plain `def` so FastAPI schedules it in a threadpool.
- JSON responses stream Polars' own `frame.write_json()` directly into the
  response body rather than calling `to_dicts()` and rebuilding a Pydantic list,
  which previously doubled peak memory.
- Shared helpers live in `app/utils/` (`dates`, `params`, `errors`, `phones`,
  `companies`). Lifespan hooks are placeholders; all work is synchronous
  in-request.

## Consequences

**Positive**

- One dependency-light process; `pytest` + Starlette `TestClient` exercises the
  full stack without external services.
- Strict schemas make the API contract explicit and catch unknown options or
  mistyped fields early.
- Polars expressions and direct JSON serialization keep memory close to upload
  size and keep `/health` responsive during large cleans.

**Trade-offs**

- Pure service functions occasionally need a small Python row loop (for example
  phone quarantine records); these deliberately avoid `to_dicts()`.
- FastAPI's raw `Response` returns in JSON/CSV/ZIP modes bypass response-model
  validation, so the rendering helper must be kept consistent by tests.

## Code references

`app/main.py`, `app/routers/{clean,validate,demo}.py`, `app/schemas/common.py`,
`app/services/{csv_cleaner,json_validator}.py`, `static/index.html`,
`requirements.txt`.
