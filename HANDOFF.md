# HANDOFF — Data-Cleaning Microservice

## Architecture
- **Stack:** Python 3.12, FastAPI 0.165, Starlette 1.10, Pydantic v2, Polars 2.1, pytest 9.2, httpx 0.28. No pandas, no ORM, no DB. Exact versions pinned in `requirements.txt`.
- **Layout:** `app/main.py` (lifespan, router registration, `CsvReadError → 400` handler, `/health`). `app/routers/` (HTTP/IO only), `app/services/` (pure Polars + validation logic), `app/schemas/` (strict Pydantic v2), `app/utils/` (dates, option parsing, errors). `tests/` mirrors routers/services. `scripts/smoke.py` is the live-HTTP check.
- **Flow:** router resolves options → `read_upload_limited()` streams the upload in 64 KB chunks with a cumulative 25 MB cap (413 on overflow) → `clean_csv_bytes()` → `CleaningOutcome(frame, summary, ignored_columns, quarantined)` → JSON body or CSV bytes + `X-*` headers.
- **Workers:** none. No Celery/queues/background jobs; all work is synchronous in-request. Lifespan hooks are placeholders.
- **Conventions:** `extra="forbid"` on all schemas; unknown option columns are ignored and counted under `unknown_columns_ignored`; empty CSV cells become `null`; all CSV columns read as `pl.String` (`infer_schema_length=0`). Ragged CSV rows are quarantined with reasons, never silently truncated.

## Data schemas
- `SummaryReport`: `rows_in`, `rows_out`, `rows_dropped`, `fixes_applied: dict[str,int]` (keys: `emails_normalized`, `dates_normalized`, `unparseable_dates`, `duplicates_removed`, `malformed_rows`, optional `rows_missing_dedupe_key`, `unknown_columns_ignored`).
- `CleanOptions`: `dedupe_by: str | None`, `date_columns: list[str]`, `email_columns: list[str]`, `output_format: Literal["json","csv"]`. Lists accept repeated fields, comma strings, or JSON arrays.
- `CleanJsonResponse`: `data: list[dict]`, `summary: SummaryReport`, `quarantined: list[QuarantinedRecord]`.
- `FieldRule`: `required: bool`, `type: Literal["string","email","date","number","integer","boolean"]`, `nullable: bool`.
- `ValidateRequest`: `records: list[dict]`, `field_rules: dict[str, FieldRule]`.
- `QuarantinedRecord`: `row_index: int`, `record: Any` (raw field list for CSV, object/value for JSON), `errors: list[str]`. Defined in `app/schemas/common.py`, re-exported by `app/schemas/validate.py` for the validator.
- `ValidateSummary`: canonical 4 keys (`fixes_applied: dict[str,int]`, `{}` when no auto-fixes apply) + `valid_count`, `quarantined_count`, `errors_by_field: dict[str,int]`.
- `ValidateResponse`: `valid`, `quarantined`, `summary`.
- **Clean-mode invariant:** `rows_in == rows_out + rows_dropped + len(quarantined)`. Malformed rows count in `rows_in` but never in `rows_out`; `rows_dropped` covers dedupe removals only. For CSV quarantines, `row_index` is 0-based among data rows (header excluded) and `record` is the raw field list, e.g. errors `"malformed row: expected 2 columns, found 3"`.

## Completed endpoints
- `GET /health` → `{"status":"ok"}`.
- `POST /clean/csv` — multipart `file` + form/query options. JSON mode returns cleaned records, summary, and quarantined rows; CSV mode returns `text/csv` with `X-Rows-In/Out/Dropped`, `X-Quarantined-Rows`, `X-Fixes-Applied`, `X-Unknown-Columns-Ignored`, `Content-Disposition`. Handles UTF-8 ±BOM, Latin-1 fallback, empty/BOM-only files, blank rows, 13 date formats, unparseable-date preservation, trim/lowercase email + dedupe key, and malformed-row quarantine.
- `POST /validate/json` — returns `valid`, `quarantined` (all per-record errors), `summary` (`fixes_applied` is `{}`).
- **Verification:** `python -m pytest -v` → **65 passed**; `python scripts/smoke.py` → `smoke OK` (boots uvicorn and exercises all three endpoints over real HTTP).

## Recently completed
1. **Malformed-row accounting:** `decode_csv_bytes()` separates decoding from parsing; `_filter_malformed_rows()` detects ragged records with `csv.reader` and quarantines them with reasons before Polars parses the frame. Added `malformed_rows` to `fixes_applied`, `quarantined` to `CleanJsonResponse`, `X-Quarantined-Rows` to CSV mode, and the reconciling summary arithmetic. Unparseable dates are still preserved/counted, not quarantined.
2. **Upload guard:** `read_upload_limited()` streams `UploadFile` in 64 KB chunks with a cumulative 25 MB cap, rejecting with 413 before buffering; the post-read size check was removed from the service.
3. **Reproducibility + live integration:** pinned exact versions in `requirements.txt`, added `scripts/smoke.py`, and unified `fixes_applied` as `dict[str,int]` across both summaries.

## Known bugs / limitations
1. **Test-client multipart quirk.** Installed `httpx2` drops the `file` part when `client.post(files=..., data=[(key, value), ...])` is used (returns 422 "file required"). `test_clean_list_options_via_repeated_form_fields` works around it with a raw multipart body.
2. Date format `%d/%m/%Y` precedes `%m/%d/%Y`; ambiguous values such as `03/04/2024` are day-first by design (documented in `app/utils/dates.py`).
3. `read_csv_bytes()` is a frame-only wrapper; callers needing quarantine reasons must use `read_csv_bytes_with_quarantine()`.

## Next steps
None assigned. The remaining limitations above are the natural candidates (multipart workaround, date disambiguation, wrapper API).
