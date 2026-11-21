# HANDOFF — Data-Cleaning Microservice

## Architecture
- **Stack:** Python 3.12, FastAPI 0.165, Starlette 1.10, Pydantic v2, Polars 2.1, pytest 9.2, httpx2 2.16.2 (the client Starlette's `TestClient` imports). No pandas, no ORM, no DB. Exact versions pinned in `requirements.txt`.
- **Layout:** `app/main.py` (lifespan, router registration, `CsvReadError → 400` handler, `/health`). `app/routers/` (HTTP/IO only), `app/services/` (pure Polars + validation logic), `app/schemas/` (strict Pydantic v2), `app/utils/` (dates, option parsing, errors). `tests/` mirrors routers/services. `scripts/smoke.py` is the live-HTTP check.
- **Flow:** router resolves options → `read_upload_limited()` streams the upload in 64 KB chunks with a cumulative 25 MB cap (413 on overflow) → `clean_csv_bytes()` → `CleaningOutcome(frame, summary, ignored_columns, quarantined)` → JSON body or CSV bytes + `X-*` headers.
- **Workers:** none. No Celery/queues/background jobs; all work is synchronous in-request. Lifespan hooks are placeholders.
- **Conventions:** `extra="forbid"` on all schemas; unknown option columns are preserved in `summary.warnings.unknown_columns`; empty CSV cells become `null`; all CSV columns read as `pl.String` (`infer_schema_length=0`). Ragged CSV rows are quarantined with reasons, never silently truncated. Content-Length is capped in middleware (2 MB for `/validate/json`, 25 MB + multipart overhead for `/clean/csv`); uploads are also capped in 64 KB streaming chunks with a `file.size` pre-check.

## Data schemas
- `WarningSummary` (shared): `malformed_rows: int`, `unknown_columns: list[str]` — always present on both summaries.
- `SummaryReport`: `rows_in`, `rows_out`, `rows_dropped`, `blank_rows_skipped`, `fixes_applied: dict[str,int]` (`emails_normalized`, `dates_normalized`, `unparseable_dates`, `duplicates_removed`, optional `ambiguous_dates` / `rows_missing_dedupe_key`), `warnings: WarningSummary`.
- `CleanOptions`: `dedupe_by`, `dedupe_ignore_case` (default `False`), `date_columns`, `date_dayfirst` (default `True`), `email_columns`, `output_format`, `delimiter` (`,`, `;`, tab, `|`), `include_quarantined` (ZIP companion in CSV mode). Lists accept repeated fields, repeated query params, comma strings, or JSON arrays.
- `CleanJsonResponse`: `data: list[dict]`, `summary: SummaryReport`, `quarantined: list[QuarantinedRecord]`.
- `FieldRule`: `required: bool`, `type: Literal["string","email","date","number","integer","boolean"]`, `nullable: bool`.
- `ValidateRequest`: `records: list[dict]` (max 1000), `field_rules: dict[str, FieldRule]`. Validation never coerces values; numeric strings must match `^[+-]?\d+(\.\d+)?$`, and emails longer than 254 chars are rejected before the regex.
- `QuarantinedRecord`: `row_index: int`, `record: Any` (raw field list for CSV, object/value for JSON), `errors: list[str]`. Defined in `app/schemas/common.py`, re-exported by `app/schemas/validate.py` for the validator.
- `ValidateSummary`: `rows_in`, `rows_out`, `rows_dropped` (always `0` — quarantines are reported separately), `fixes_applied: dict[str,int]` (`{}`), `valid_count`, `quarantined_count`, `errors_by_field: dict[str,int]`, `warnings: WarningSummary`.
- `ValidateResponse`: `valid`, `quarantined`, `summary`.
- **Shared invariant:** `rows_in == rows_out + rows_dropped + len(quarantined)` on both endpoints (on `/validate/json`, `len(quarantined)` is `quarantined_count`). Malformed and quarantined rows count in `rows_in` but never in `rows_out`; blank CSV rows are skipped and counted in `rows_dropped`. For CSV quarantines, `row_index` is 0-based among data rows (header excluded) and `record` is the raw field list, e.g. errors `"malformed row: expected 2 columns, found 3"`.

- **Dates:** both endpoints accept the same `DATE_FORMATS` list from `app/utils/dates.py`; offset/fractional-second timestamps and the old `fromisoformat` fallback are intentionally unsupported. The cleaner selects one day-first or month-first format per ambiguous numeric pair from the whole column and reports `ambiguous_dates`; `date_dayfirst` breaks ties.

## Completed endpoints
- `GET /health` → `{"status":"ok"}`.
- `POST /clean/csv` — multipart `file` + form/query options. JSON mode returns cleaned records, summary, and quarantined rows; CSV mode returns `text/csv` with `X-Rows-In/Out/Dropped`, `X-Blank-Rows-Skipped`, `X-Quarantined-Rows`, `X-Fixes-Applied`, `X-Unknown-Columns-Ignored`, `X-Warnings`, `Content-Disposition`. With `include_quarantined=true`, CSV mode instead returns `application/zip` containing `cleaned.csv` and `quarantined.csv`. Cells starting with `= + - @` are quoted in CSV output only. Handles UTF-8 ±BOM, cp1252/Latin-1 fallback, delimiter sniffing, empty/BOM-only files, blank rows, case-sensitive dedupe by default, per-column date formats, unparseable-date preservation, and malformed-row quarantine.
- `POST /validate/json` — returns `valid`, `quarantined` (all per-record errors), `summary` (`fixes_applied` is `{}`). `FieldRule.nullable` lets `null` satisfy a required field; emails/numerics are strictly checked; at most 1000 records per request.
- **Verification:** `python -m pytest -v` → **124 passed**; `python scripts/smoke.py` → `smoke OK` (boots uvicorn and exercises all three endpoints over real HTTP).

## Recently completed
1. **Audit Phase 2:** cp1252 decode fallback, delimiter sniffing + `delimiter` option, header whitespace/duplicate checks, true `rows_in` with blank-row skipping and reconciliation, and direct Polars JSON serialization for lower peak memory.
2. **Audit Phase 3:** case-sensitive dedupe by default with `dedupe_ignore_case`, null/blank dedupe keys preserved, one format per ambiguous date pair with `date_dayfirst` and `ambiguous_dates`.
3. **Audit Phase 4:** strict email/numeric validation with caps, honored `nullable`, shared date format list, shared `WarningSummary`, aligned row invariant, repeated query params, ZIP quarantine companion, upload/body caps, and CSV formula escaping.

## Known bugs / limitations
1. **Body-size guards are header-based.** The middleware relies on `Content-Length`; a client can omit it. The router's streaming cap and `file.size` check still bound handler memory. Enforce the real limit at the proxy as well.
2. **Quarantine ZIP is opt-in.** Plain CSV mode still returns only an `X-Quarantined-Rows` count; use `include_quarantined=true` or JSON mode to recover the bad rows.
3. `read_csv_bytes()` is a frame-only wrapper; callers needing quarantine reasons must use `read_csv_bytes_with_quarantine()`.

## Next steps
None assigned.
