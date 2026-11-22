# HANDOFF — data-cleaner-api

FastAPI microservice that cleans messy CRM/lead CSV exports and validates JSON payloads for RevOps and data analysts. Contributor rules live in `AGENTS.md`; the reasoning behind the stack and data contracts is recorded in six ADRs indexed at `docs/adr/README.md`.

## Architecture

- **Stack:** Python 3.12 locally, 3.11 in CI. FastAPI 0.165, Starlette 1.10, Uvicorn 0.54, Pydantic v2 (2.14.4), Polars 2.1, `phonenumbers` 9.0.44, `httpx2` 2.16.2, pytest 9.2. Exact pins in `requirements.txt`. No pandas, no ORM, no database, no Node/npm build step.
- **Layout:** `app/main.py` (lifespan, `/`, `/health`, `/static` mount, body-size middleware, `CsvReadError → 400` handler). `app/routers/` — `clean.py`, `validate.py`, `demo.py` (HTTP/IO only). `app/services/` — `csv_cleaner.py`, `json_validator.py` (pure transforms). `app/schemas/` — `common.py`, `clean.py`, `validate.py` (strict Pydantic v2). `app/utils/` — `dates.py`, `params.py`, `errors.py`, `phones.py`, `companies.py`. `static/index.html` (single-file UI). `test_messy_leads.csv` (repo sample). `tests/` mirrors routers/services plus `utils/` and `test_static.py`. `docs/adr/`, `.github/workflows/test.yml`, `scripts/smoke.py`.
- **Request flow:** router resolves options (form values win over query; only *supplied* fields are passed so presets can use `model_fields_set`) → `read_upload_limited()` streams in 64 KB chunks with a 25 MB cap and `file.size` pre-check → `clean_csv_bytes()` → `build_clean_response()`, shared by `/clean/csv` and `/demo/messy-leads` for identical JSON/CSV/ZIP bodies and headers.
- **Limits:** middleware rejects oversized requests by `Content-Length` — 2 MB for `/validate/json`, 26 MB (25 MB upload + multipart overhead) elsewhere — with 413. CLI work is offloaded with `run_in_threadpool`; `validate_json` is a plain `def`, so `/health` stays responsive.
- **Conventions:** every model derives from `StrictModel` (`extra="forbid"`); CSV columns are read as `pl.String` (`infer_schema_length=0`); empty cells become `null`; JSON responses stream `frame.write_json()` directly (no `to_dicts()`); unknown option columns are preserved in `warnings.unknown_columns`; ragged/invalid rows are quarantined with reasons, never silently dropped.
- **Workers:** none. Lifespan hooks are placeholders; all work is synchronous in-request.
- **CI:** `.github/workflows/test.yml` runs `pytest -v` on pushes and PRs to `main` (Python 3.11, pip cache).

## Active endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | Single-file frontend served via `FileResponse` (hidden from OpenAPI) |
| GET | `/health` | Liveness → `{"status": "ok"}` |
| GET | `/docs` | FastAPI interactive API docs |
| POST | `/clean/csv` | Clean uploaded CSV; JSON, CSV or ZIP output |
| POST | `/validate/json` | Validate up to 1000 JSON records against field rules |
| POST | `/demo/messy-leads` | Clean the bundled dirty sample with no upload required |

`/clean/csv` options (form fields or query params; lists accept repeated fields, comma strings or JSON arrays): `dedupe_by`, `dedupe_ignore_case` (default `False`), `date_columns`, `date_dayfirst` (default `True`), `email_columns`, `phone_columns`, `company_columns`, `fuzzy_dedupe` (default `False`), `default_country_code` (default `US`, uppercased), `preset` (`hubspot` / `salesforce`), `output_format` (`json` / `csv` / `zip`), `delimiter`, `include_quarantined`. Presets fill only empty fields — explicit caller values always win — and set `dedupe_by="Email"` + `dedupe_ignore_case=True` when an Email column is present.

**Exports:**

- `json` → `{data, summary, quarantined}`.
- `csv` → `text/csv` (`cleaned.csv`) with `X-Rows-In/Out/Dropped`, `X-Blank-Rows-Skipped`, `X-Quarantined-Rows`, `X-Fixes-Applied`, `X-Unknown-Columns-Ignored`, `X-Warnings`, `Content-Disposition`.
- `zip` → deflate-compressed `cleaned_bundle.zip` with `cleaned.csv`, `quarantined.csv` (`row_index, errors, *original_headers`, header-only when empty) and `summary.json`. The older `include_quarantined=true` two-file ZIP (`cleaned.csv` + `quarantined.csv`) is preserved; `output_format=zip` takes precedence if both are set.
- Every successful response (including CSV/ZIP downloads) carries `X-Processing-Time-Ms`.

## UI (`static/index.html`)

Tailwind CSS via CDN + vanilla JavaScript, no build step. Drag-and-drop or browse for a CSV, editable raw textarea synced with file/sample, "Load Messy Sample", "Clear" and "Run Server Demo (/demo/messy-leads)"; the demo button also loads the matching inline sample so the diff view has raw rows to match against. A "Chaos Lab" panel adds category chips (case-variant duplicates, bad dates, broken emails, formula injection, ragged/blank rows) plus presets and an "Inject Chaos" button that appends ~14 realistic dirty rows to the textarea via a small client-side CSV parser/encoder. Option controls include preset, phone/company columns, country code, fuzzy dedupe and case-insensitive dedupe. Results show a reconciliation bar (`rows_in`, `rows_out`, `rows_dropped`, quarantined count, `fixes_applied` chips, timing badge, ✓ reconciled/⚠ mismatch), a cleaned-rows table, a quarantined-rows table (row index / header-keyed raw values / individual error pills), "Download Clean CSV", "Download Quarantined CSV" (built in-browser from the JSON response in the ZIP bundle's `row_index, errors, *headers` layout), "Download ZIP Bundle", and a collapsible full JSON preview. The cleaned table infers before/after cell diffs by matching raw rows to cleaned rows in order (server returns no per-cell lineage); changed cells show the literal before value, after value and a rule-based reason, with a "Changed cells only" toggle. Formula-like cells are flagged as such without claiming they were sanitized in JSON (ADR-0004). Loading/disabled states, a success toast and an `aria-live` error alert cover failures; all row data is rendered with `textContent` to avoid HTML injection.

## Data schemas & invariants

- `StrictModel`: `ConfigDict(extra="forbid")` — typos surface as 422.
- `WarningSummary`: `malformed_rows: int`, `unknown_columns: list[str]`.
- `SummaryReport`: `rows_in`, `rows_out`, `rows_dropped`, `blank_rows_skipped`, `fixes_applied: dict[str, int]`, `warnings: WarningSummary`, `processing_time_ms: float >= 0`. `fixes_applied` base keys: `emails_normalized`, `dates_normalized`, `unparseable_dates`, `duplicates_removed`; conditional keys: `phones_normalized`, `companies_normalized`, `ambiguous_dates`, `rows_missing_dedupe_key`.
- `QuarantinedRecord` (defined in `app/schemas/common.py`, re-exported by `app/schemas/validate.py`): `row_index: int`, `record: Any` (raw field list for CSV, original object/value for JSON), `errors: list[str]`. For CSV, `row_index` is 0-based among data rows, header excluded.
- `CleanOptions`: fields listed above; `output_format` is `Literal["json","csv","zip"]`, `preset` is `Literal["hubspot","salesforce"] | None`.
- `CleanJsonResponse`: `data: list[dict]`, `summary: SummaryReport`, `quarantined: list[QuarantinedRecord]`.
- `FieldRule`: `required`, `type` (`string`, `email`, `date`, `number`, `integer`, `boolean`), `nullable`. `ValidateRequest`: `records: list[dict]` (max 1000), `field_rules: dict[str, FieldRule]`. Validation never coerces values; numeric strings must match `^[+-]?\d+(\.\d+)?$`; emails over 254 chars are rejected before the regex.
- `ValidateSummary`: `rows_in`, `rows_out`, `rows_dropped` (always `0`), `valid_count`, `quarantined_count`, `errors_by_field: dict[str,int]`, `fixes_applied` (`{}`), `warnings`, `processing_time_ms`. `ValidateResponse`: `valid`, `quarantined`, `summary`.
- **Shared invariant (both endpoints):** `rows_in == rows_out + rows_dropped + len(quarantined)` — on `/validate/json` the last term is `quarantined_count`. `rows_in` comes from `csv.reader` input records, not the Polars frame. Blank rows are skipped and counted in `blank_rows_skipped` and `rows_dropped`; quarantined rows are never counted as drops. Invalid-phone rows are removed from the frame and quarantined, so `len(phone_quarantines)` is explicitly subtracted in `clean_csv_bytes` to avoid double counting. Ambiguous numeric dates use one interpretation per column (`date_dayfirst` breaks ties), and unparseable values are preserved verbatim and counted in `unparseable_dates`.

## Verification

- `python -m pytest -v` (2026-11-22): **216 passed**, 0 failed, 0 skipped, 1.85 s.
- Runtime invariant probe against the live app: `/clean/csv` and `/demo/messy-leads` each `7 in = 3 out + 3 dropped + 1 quarantined`; `/validate/json` `2 in = 1 out + 0 dropped + 1 quarantined`. Schema field sets and `dict[str,int]` fixes verified.
- `python scripts/smoke.py` exercises the endpoints over real HTTP.

## Completed since previous handoff

1. `5a9e71a` — single-file web UI, `GET /` + `/static`, static route tests.
2. `2a9ac67` — `processing_time_ms` telemetry, `/demo/messy-leads`, CI workflow.
3. `b382dd4` — E.164 phone normalization, HubSpot/Salesforce presets, ZIP bundle export.
4. `e12a68a` — company entity normalization and fuzzy deduplication.
5. `fe036e8` — six ADRs documenting the contracts and trade-offs.
6. Uncommitted — UI "wow-factor" pass in `static/index.html`: Chaos Lab injection, inferred cell-level before/after diffs with changed-only toggle, quarantine reason pills, and a client-generated quarantined CSV export. Backend contracts untouched.

## Known bugs / tech debt / deferred edge cases

1. **Body-size guards are header-based.** The middleware relies on `Content-Length`, which a client can omit. The chunked upload cap and `file.size` pre-check still bound handler memory; enforce the real limit at the reverse proxy.
2. **`warnings.malformed_rows` merges signals.** It equals the total quarantine count, including invalid-phone rows as well as structurally ragged CSV rows; callers cannot distinguish them from that field alone.
3. **Plain CSV hides quarantine details.** Recovering rejected rows requires JSON mode, `include_quarantined=true` (two-file ZIP), or `output_format=zip`.
4. **Preset header matching is exact** against whitespace-stripped names; renamed CRM columns need explicit options.
5. **Per-cell Python cost:** `fuzzy_dedupe` and company normalization use `map_elements`; both are opt-in so the default path stays vectorized.
6. **ISO-8601 offsets and fractional-second timestamps are intentionally unsupported** by both endpoints (shared date-format list; no `fromisoformat` fallback).
7. **UI maintenance:** Tailwind is a runtime CDN dependency (offline/CDN-blocked deployments render unstyled), and the dirty sample must stay in sync in three places — `test_messy_leads.csv`, `FALLBACK_MESSY_LEADS_CSV` in `app/routers/demo.py`, and `SAMPLE_CSV` in `static/index.html`.
8. `processing_time_ms` is measured inside the pure service functions, so it excludes HTTP parsing and response serialization.
9. `read_csv_bytes()` is a frame-only wrapper; callers needing quarantine reasons must use `read_csv_bytes_with_quarantine()`.
10. **UI cell diffs are heuristic.** The API returns no per-cell lineage or source indices for cleaned rows, so `static/index.html` re-parses the submitted text and matches rows in order with a conservative similarity score. Unmatched rows are left unannotated; reason tags are inferred from the configured column roles, not returned by the server.

## Next 3 steps

1. **Enforce upload/body limits at the reverse proxy** as well as in `enforce_body_size_limit` (`app/main.py`), since `Content-Length` can be omitted. *(ADR-0003)*
2. **Split `warnings.malformed_rows`** into structural-malformed vs invalid-phone counts in `SummaryReport`/`WarningSummary` and update the reconciliation tests. *(ADR-0002)*
3. **Broaden CRM preset header matching** beyond exact stripped names (aliases/case-insensitive matching) in `apply_preset` (`app/services/csv_cleaner.py`), without overriding explicit options. *(ADR-0006)*
