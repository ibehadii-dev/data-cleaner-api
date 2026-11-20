# HANDOFF — Data-Cleaning Microservice

## Architecture
- **Stack:** Python 3.12, FastAPI 0.165, Starlette 1.10, Pydantic v2, Polars 2.1, pytest 9.2, httpx 0.28. No pandas, no ORM, no DB.
- **Layout:** `app/main.py` (lifespan, router registration, `CsvReadError → 400` handler, `/health`). `app/routers/` (HTTP/IO only), `app/services/` (pure Polars + validation logic), `app/schemas/` (strict Pydantic v2), `app/utils/` (dates, option parsing, errors). `tests/` mirrors routers/services.
- **Flow:** router parses options → `await file.read()` → `clean_csv_bytes()` → `CleaningOutcome(frame, summary, ignored_columns)` → JSON body or CSV bytes + `X-*` headers.
- **Workers:** none. No Celery/queues/background jobs; all work is synchronous in-request. Lifespan hooks are placeholders.
- **Conventions:** `extra="forbid"` on all schemas; unknown option columns are ignored and counted under `unknown_columns_ignored`; empty CSV cells become `null`; all CSV columns read as `pl.String` (`infer_schema_length=0`).

## Data schemas
- `SummaryReport`: `rows_in`, `rows_out`, `rows_dropped`, `fixes_applied: dict[str,int]` (keys: `emails_normalized`, `dates_normalized`, `unparseable_dates`, `duplicates_removed`, optional `rows_missing_dedupe_key`, `unknown_columns_ignored`).
- `CleanOptions`: `dedupe_by: str | None`, `date_columns: list[str]`, `email_columns: list[str]`, `output_format: Literal["json","csv"]`. Lists accept repeated fields, comma strings, or JSON arrays.
- `CleanJsonResponse`: `data: list[dict]`, `summary: SummaryReport`.
- `FieldRule`: `required: bool`, `type: Literal["string","email","date","number","integer","boolean"]`, `nullable: bool`.
- `ValidateRequest`: `records: list[dict]`, `field_rules: dict[str, FieldRule]`.
- `QuarantinedRecord`: `row_index: int`, `record: Any`, `errors: list[str]`.
- `ValidateSummary`: canonical 4 keys (`fixes_applied: int = 0`) + `valid_count`, `quarantined_count`, `errors_by_field: dict[str,int]`.
- `ValidateResponse`: `valid`, `quarantined`, `summary`.

## Completed endpoints
- `GET /health` → `{"status":"ok"}`.
- `POST /clean/csv` — multipart `file` + form/query options. JSON mode returns cleaned records + summary; CSV mode returns `text/csv` with `X-Rows-In/Out/Dropped`, `X-Fixes-Applied`, `X-Unknown-Columns-Ignored`, `Content-Disposition`. Handles UTF-8 ±BOM, Latin-1 fallback, empty/BOM-only files, ragged rows, 13 date formats, unparseable-date preservation, trim/lowercase email + dedupe key.
- `POST /validate/json` — returns `valid`, `quarantined` (all per-record errors), `summary`.
- **Verification:** `python -m pytest -v` → **58 passed**; `uvicorn app.main:app --host 0.0.0.0 --port 8000` boots, `/health` and `/openapi.json` return 200.

## Known bugs / limitations
1. **Ragged rows silently truncated.** `pl.read_csv(..., truncate_ragged_lines=True)` drops extra fields without counting them; no `malformed_rows` metric or quarantine list exists. Violates AGENTS.md "never silently drop malformed rows."
2. **Upload buffered before size check.** `await file.read()` loads the whole file; `MAX_FILE_BYTES` (25 MB) is enforced afterwards in `read_csv_bytes()`. Oversized uploads can exhaust memory before rejection.
3. **Test-client multipart quirk.** Installed `httpx2` drops the `file` part when `client.post(files=..., data=[(key, value), ...])` is used (returns 422 "file required"). `test_clean_list_options_via_repeated_form_fields` works around it with a raw multipart body.
4. **Summary typing inconsistent.** `fixes_applied` is `dict[str,int]` for clean vs `int` (always 0) for validate.
5. Date format `%d/%m/%Y` precedes `%m/%d/%Y`; ambiguous values such as `03/04/2024` are day-first by design (documented in `app/utils/dates.py`).

## Exact next 3 steps
1. **Fix malformed-row accounting:** detect ragged records before/around `pl.read_csv`, add `malformed_rows` to `fixes_applied`, add a `quarantined` list with reasons to `CleanJsonResponse` and a count header to CSV mode; add tests for ragged and unparseable rows.
2. **Guard uploads before buffering:** stream `UploadFile` in chunks with a cumulative 25 MB cap, reject early with 400/413; add an oversized-upload test and remove the post-read size check.
3. **Reproducibility + live integration:** pin exact versions in `requirements.txt`, add a live-uvicorn smoke script exercising both endpoints over real HTTP, and unify `fixes_applied` typing across both summaries.
