# ADR-0006: CRM presets, multi-format exports, telemetry, UI and CI

- **Status:** Accepted
- **Date:** 2026-11-21

## Context

Analysts needed zero-configuration cleaning for common CRM exports, recoverable
quarantine data in downloadable form, visibility into processing cost, and a
demo that works without supplying a file. CI had to verify all of this on every
push and pull request.

## Decision

- **CRM presets**: `CleanOptions.preset` accepts `hubspot` or `salesforce`.
  `apply_preset` maps known headers (`Email`, `Create Date`/
  `Last Activity Date`, `Phone Number`/`Mobile Phone Number`, `Company Name` for
  HubSpot; `Email`, `CreatedDate`/`LastModifiedDate`, `Phone`/`MobilePhone`,
  `Company`/`Account Name` for Salesforce) and sets `dedupe_by="Email"` plus
  `dedupe_ignore_case=True` when `Email` is present. Only empty/unset fields are
  filled: the router passes explicitly supplied options only, and
  `model_fields_set` lets the service distinguish caller intent from defaults.
- **Exports**: `output_format` accepts `json`, `csv`, or `zip`. The ZIP bundle
  (`ZIP_DEFLATED`) contains `cleaned.csv` (with formula escaping),
  `quarantined.csv` (`row_index, errors, *original_headers`, header-only when
  empty, cells escaped) and `summary.json` (`model_dump_json(indent=2)`). It is
  returned as `application/zip` with `Content-Disposition:
  attachment; filename="cleaned_bundle.zip"` and `X-Processing-Time-Ms`. The
  older `include_quarantined=true` two-file companion ZIP is preserved. All
  response variants are produced by the shared `build_clean_response` helper
  used by both `/clean/csv` and `/demo/messy-leads`.
- **Telemetry**: `processing_time_ms: float = Field(default=0.0, ge=0.0)` is on
  both `SummaryReport` and `ValidateSummary`, measured with
  `time.perf_counter()` inside `clean_csv_bytes` and `validate_records` and
  rounded to two decimals. The same value is returned in the
  `X-Processing-Time-Ms` header on every successful response, including CSV and
  ZIP downloads.
- **Demo endpoint**: `POST /demo/messy-leads` reads `test_messy_leads.csv` from
  the repository root and falls back to an equivalent inline string on any I/O
  error, so minimal deployments never 500. It reuses the cleaning pipeline with
  email dedupe, case-insensitive keys, and `created_at`/`email` normalization.
- **UI**: `static/index.html` exposes `preset`, `phone_columns`,
  `company_columns`, `default_country_code`, `fuzzy_dedupe`, a
  "Run Server Demo (/demo/messy-leads)" button, a "Download ZIP Bundle" button,
  and a `processing_time_ms` timing badge.
- **CI**: `.github/workflows/test.yml` runs on pushes and pull requests to
  `main` using `actions/checkout@v4`, `actions/setup-python@v5` (Python 3.11,
  pip cache), `pip install -r requirements.txt`, and `pytest -v`; the README
  shows the workflow badge.

## Consequences

**Positive**

- Presets reduce setup to one option while explicit caller values always win.
- ZIP bundles are self-describing: cleaned data, rejected rows, and the summary
  travel together; telemetry is observable even when there is no JSON body.
- The demo and CI make regressions obvious without external infrastructure.

**Trade-offs**

- `output_format=zip` takes precedence over `include_quarantined`; callers must
  choose one export style.
- Preset header matching is exact against stripped header names, so renamed
  CRM columns still require explicit options.

## Code references

`app/schemas/clean.py`, `app/schemas/common.py`, `app/schemas/validate.py`,
`app/services/csv_cleaner.py` (`PRESET_COLUMNS`, `apply_preset`,
`render_bundle_quarantined_csv`), `app/routers/clean.py` (`_resolve_options`,
`build_clean_response`), `app/routers/demo.py`, `app/routers/validate.py`,
`static/index.html`, `.github/workflows/test.yml`, `README.md`,
`tests/routers/`.
