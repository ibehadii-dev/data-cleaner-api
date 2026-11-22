# data-cleaner-api

[![test](https://github.com/ibehadii-dev/data-cleaner-api/actions/workflows/test.yml/badge.svg)](https://github.com/ibehadii-dev/data-cleaner-api/actions/workflows/test.yml)

A small FastAPI service that cleans messy CRM/lead CSV exports and validates JSON payloads. It returns the cleaned records **and** a summary of everything that happened to them — including a quarantined row for anything that could not be trusted.

Built as a student project with production-grade engineering discipline: **FastAPI** for the HTTP layer, **Polars** for tabular transforms, **Pydantic v2** for strict request/response contracts, and **pytest** for verification. No pandas, no database, no build step.

## TL;DR

- **Nothing disappears quietly.** Every run satisfies `rows_in == rows_out + rows_dropped + len(quarantined)`.
- **Bad rows get a paper trail.** Malformed or invalid rows are quarantined with their original values and a reason.
- **Three ways out.** JSON for pipelines, CSV for spreadsheets, and a self-describing ZIP bundle with cleaned data, quarantined rows, and the summary.
- **Observable.** Every response carries `processing_time_ms` in the summary and an `X-Processing-Time-Ms` header.
- **Zero-setup demo.** `POST /demo/messy-leads` runs a deliberately dirty sample through the real pipeline with no upload required.

## Why bad data breaks pipelines — and how this pushes back

### Row counts actually reconcile

Most cleaning scripts derive `rows_in` from the dataframe they produced, which makes the numbers look consistent even when rows were silently dropped or invented. Here, `rows_in` is counted from the **input records**, and the summary is checked against a single invariant on both endpoints:

> `rows_in == rows_out + rows_dropped + len(quarantined)`
>
> On `/validate/json`, the last term is `quarantined_count`. Blank CSV rows are skipped and counted in `blank_rows_skipped` and `rows_dropped`; quarantined rows are reported separately and never counted as drops.

The parser enforces this with shape and row-count checks against the original input (`app/services/csv_cleaner.py`), and the contract is asserted directly in the test suite.

### Rejected rows are recoverable

Anything that cannot be parsed or validated becomes a `QuarantinedRecord`:

```json
{ "row_index": 1, "record": ["2", "Bob", "extra"], "errors": ["malformed row: expected 2 columns, found 3"] }
```

`row_index` is 0-based among CSV data rows (header excluded); `record` is the raw field list for CSV and the original object for JSON. Phone numbers that fail E.164 normalization are quarantined the same way, with a region-specific error. Dates such as `TBD` never raise: unparseable values are preserved verbatim and counted in `fixes_applied.unparseable_dates`. For ambiguous numeric dates, one interpretation is selected per column from the whole column's evidence, with `date_dayfirst` breaking ties and `ambiguous_dates` reporting the uncertainty.

### Ingestion and output are defensive

- **Encoding:** UTF-8 (with or without BOM), then Windows-1252, then Latin-1; unreadable or binary files return a clear `400`.
- **Structure:** delimiter sniffing across `,`, `;`, tab and `|` (or an explicit `delimiter` option), header whitespace stripping, and a `400` on duplicate headers or a single-column header that clearly contains a delimiter.
- **Bounds:** 25 MB upload cap with chunked reads and a `file.size` pre-check; 2 MB body cap for `/validate/json`; oversized requests return `413`. CPU-bound work is offloaded from the event loop so `/health` stays responsive.
- **Safe exports:** cells beginning with `=`, `+`, `-` or `@` are quoted in **CSV output only**, preventing spreadsheet formula injection while leaving JSON values unchanged.
- **Strict schemas:** every Pydantic model uses `extra="forbid"`, so misspelled options and payload fields surface as `422` instead of being ignored.

## See it in action

Open the single-file UI at <http://localhost:8000/> — drag in a CSV or paste raw text, adjust options, and inspect the reconciliation badge, quarantine table, and timing badge. Interactive API docs are at <http://localhost:8000/docs>, and the liveness check is at <http://localhost:8000/health>.

The "Run Server Demo" button in the UI calls `POST /demo/messy-leads`, which cleans a bundled sample (whitespace, case-variant duplicates, `TBD` dates, a missing email, one ragged row and blank rows) through the same pipeline as a real upload. The service tests pin the result at 7 rows in, 3 out, 1 quarantined.

### Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Liveness check (`{"status": "ok"}`) |
| `POST` | `/clean/csv` | Clean an uploaded CSV; JSON, CSV or ZIP output |
| `POST` | `/validate/json` | Validate up to 1000 JSON records against field rules |
| `POST` | `/demo/messy-leads` | Clean the bundled messy-leads sample, no upload required |

```bash
curl -X POST http://localhost:8000/demo/messy-leads
curl -X POST 'http://localhost:8000/demo/messy-leads?output_format=csv'
curl -X POST 'http://localhost:8000/demo/messy-leads?output_format=zip'
```

### Cleaning options

Options for `/clean/csv` may be sent as multipart form fields or query parameters (form values win). List options accept repeated fields/parameters, comma-separated strings, or JSON arrays. Unknown or misspelled column names are preserved in `summary.warnings.unknown_columns` instead of being discarded.

| Option | Default | Purpose |
| --- | --- | --- |
| `dedupe_by` | none | Column whose first occurrence per key is kept; blank keys are preserved. |
| `dedupe_ignore_case` | `false` | Compare keys case-insensitively (off by default to protect case-sensitive IDs). |
| `date_columns` | none | Columns normalized to `YYYY-MM-DD`; unparseable values are kept and counted. |
| `date_dayfirst` | `true` | Break ties when a numeric date fits both day-first and month-first readings. |
| `email_columns` | none | Columns trimmed and lowercased. |
| `phone_columns` | none | Columns normalized to E.164 using `phonenumbers`; invalid values are quarantined. |
| `company_columns` | none | Columns tidied by collapsing whitespace and stripping trailing legal suffixes (`Inc.`, `LLC`, `Ltd`, `Corp`, `GmbH`, …). |
| `fuzzy_dedupe` | `false` | Compare `dedupe_by` keys as normalized entity names, so `Acme Corp.` and `ACME, Inc.` collapse to the first row. |
| `default_country_code` | `US` | Two-letter region used for phone normalization (uppercased). |
| `preset` | none | `hubspot` or `salesforce`: auto-detect email, date, phone and company columns from known CRM headers. Explicit options always win. |
| `delimiter` | auto | One of `,`, `;`, tab or `|`; sniffs from the header when omitted. |
| `output_format` | `json` | `json`, `csv` or `zip`. |
| `include_quarantined` | `false` | With `csv` output, return a two-file ZIP of `cleaned.csv` + `quarantined.csv`. |

CRM presets recognize these headers (matched exactly after stripping whitespace):

| Preset | Email | Dates | Phones | Companies |
| --- | --- | --- | --- | --- |
| `hubspot` | `Email` | `Create Date`, `Last Activity Date` | `Phone Number`, `Mobile Phone Number` | `Company Name` |
| `salesforce` | `Email` | `CreatedDate`, `LastModifiedDate` | `Phone`, `MobilePhone` | `Company`, `Account Name` |

When an `Email` column is present, presets also set `dedupe_by="Email"` and `dedupe_ignore_case=true` unless those were supplied explicitly.

### Exports and response shape

- `output_format=json` returns `{data, summary, quarantined}`.
- `output_format=csv` streams `cleaned.csv` with `X-Rows-In/Out/Dropped`, `X-Blank-Rows-Skipped`, `X-Quarantined-Rows`, `X-Fixes-Applied`, `X-Unknown-Columns-Ignored`, `X-Warnings` and `Content-Disposition` headers.
- `output_format=zip` returns a deflate-compressed `cleaned_bundle.zip` containing `cleaned.csv`, `quarantined.csv` (`row_index, errors, *original_headers`) and `summary.json`. The older `include_quarantined=true` two-file companion ZIP is preserved; `zip` takes precedence if both are set.

```bash
curl -X POST -F 'file=@leads.csv' -F 'preset=hubspot' \
  -F 'default_country_code=GB' -F 'output_format=zip' \
  http://localhost:8000/clean/csv -o cleaned_bundle.zip
```

```json
{
  "data": [{ "email": "ada@example.com", "created_at": "2024-03-15" }],
  "summary": {
    "rows_in": 3,
    "rows_out": 1,
    "rows_dropped": 1,
    "blank_rows_skipped": 0,
    "fixes_applied": {
      "emails_normalized": 1,
      "dates_normalized": 1,
      "unparseable_dates": 1,
      "duplicates_removed": 1
    },
    "warnings": { "malformed_rows": 1, "unknown_columns": [] },
    "processing_time_ms": 0.42
  },
  "quarantined": [
    { "row_index": 1, "record": ["2", "Bob", "extra"], "errors": ["malformed row: expected 2 columns, found 3"] }
  ]
}
```

`/validate/json` accepts `{"records": [...], "field_rules": {...}}` where each rule has `required`, `type` (`string`, `email`, `date`, `number`, `integer`, `boolean`) and `nullable`. It never coerces values: a numeric string is checked against a strict decimal pattern but returned unchanged. Invalid records are quarantined with all of their errors, and the same row invariant applies.

## Engineering discipline

The project moved quickly, but the contracts did not. A few deliberate habits kept it verifiable:

- **`AGENTS.md`** defines the stack, directory layout, data rules and a hard requirement that every transform and endpoint be tested for empty inputs, missing values and duplicate keys.
- **`HANDOFF.md`** records the architecture, schemas, completed endpoints, known limitations and verification commands — so context survives between sessions.
- **Plan → build workflow.** Changes are inspected and scoped before implementation; only the intended files are touched.
- **Strict schemas.** `extra="forbid"` on every Pydantic model turns payload typos into `422` responses at the boundary.
- **Six Architecture Decision Records** capture the reasoning behind the stack, reconciliation, ingestion guardrails, validation hardening, normalization rules, and presets/exports/telemetry.
- **Tests on every push.** The suite covers routers, services and utils, including encoding fallbacks, malformed rows, deduplication, date ambiguity, formula escaping and export bundles. `pytest -v` reports 216 passing tests, and `.github/workflows/test.yml` reruns it on pushes and pull requests to `main`.

## Quickstart & testing

Requires Python 3.11+ (the version used in CI).

```bash
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

- UI: <http://localhost:8000/>
- API docs: <http://localhost:8000/docs>
- Health: <http://localhost:8000/health>
- Demo: `curl -X POST http://localhost:8000/demo/messy-leads`

```bash
pytest -v
python scripts/smoke.py   # optional: boot uvicorn and exercise all endpoints over real HTTP
```

## What's next

These are grounded in the repository's documented limitations and ADR trade-offs rather than a feature wishlist:

- [ ] Enforce upload and body size limits at the reverse proxy as well as in the app's `Content-Length` middleware, which a client can omit.
- [ ] Make quarantined rows easier to recover from plain CSV mode (currently opt-in via `include_quarantined=true`, JSON mode, or the ZIP bundle).
- [ ] Broaden CRM preset header matching beyond exact stripped names, so renamed columns need fewer explicit options.
- [ ] Reduce the per-cell Python cost of `fuzzy_dedupe` and company normalization (both use `map_elements` and are opt-in today).
- [ ] Extend company normalization beyond conservative suffix and casing rules without giving up its no-extra-dependency design.
- [ ] Revisit ISO-8601 offsets and fractional-second timestamps; both endpoints currently reject them by design to stay consistent.
- [ ] Optionally coerce validated numeric strings on request; today they are validated strictly but returned unchanged.
- [ ] Separate structurally malformed rows from invalid-phone rows in `warnings.malformed_rows`, which currently equals the total quarantine count.

## Docs

- [Architecture Decision Records](docs/adr/README.md) — ADR-0001 through ADR-0006.
- [HANDOFF.md](HANDOFF.md) — architecture, schemas, known limitations and verification.
- [AGENTS.md](AGENTS.md) — coding and data rules for contributors.
- [AUDIT_FIXES.md](AUDIT_FIXES.md) — the 21-item audit checklist and its verification.
