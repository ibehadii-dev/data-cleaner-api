# data-cleaner-api

[![test](https://github.com/ibehadii-dev/data-cleaner-api/actions/workflows/test.yml/badge.svg)](https://github.com/ibehadii-dev/data-cleaner-api/actions/workflows/test.yml)

FastAPI microservice that cleans messy CSV exports and validates JSON payloads for RevOps and data analysts. Built with Polars and Pydantic v2 — no pandas, no database.

## Quickstart

```bash
python -m pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open <http://localhost:8000/> for the single-file frontend, or <http://localhost:8000/docs> for the interactive API docs. Run the suite with `pytest -v`.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Liveness check |
| `POST` | `/clean/csv` | Clean an uploaded CSV (JSON, CSV or ZIP output) |
| `POST` | `/validate/json` | Validate a batch of JSON records |
| `POST` | `/demo/messy-leads` | Zero-friction demo using the bundled messy-leads sample |

Every summary preserves the reconciliation invariant:

```
rows_in == rows_out + rows_dropped + len(quarantined)
```

Malformed rows are quarantined with a reason and never silently dropped.

### `POST /clean/csv`

Multipart CSV upload. Options may be sent as form fields or as query parameters (form values win); unrecognised columns are ignored and reported in the summary.

| Option | Purpose |
| --- | --- |
| `phone_columns` | Repeated field naming columns whose phone numbers are normalised. |
| `default_country_code` | Two-letter country code used when normalising phones (default `US`; normalised to upper case). |
| `preset` | `hubspot` or `salesforce`. Auto-detects email, date and phone columns from known CRM headers; explicitly supplied options always win. |
| `output_format` | `json` (default), `csv`, or `zip`. |

`output_format=json` returns `{data, summary, quarantined}`. `output_format=csv` downloads `cleaned.csv` (add `include_quarantined=true` to get a ZIP with `cleaned.csv` + `quarantined.csv`). `output_format=zip` downloads a bundle containing:

- `cleaned.csv` — the cleaned rows
- `quarantined.csv` — rejected rows with a reason
- `summary.json` — the summary report

```bash
curl -X POST -F 'file=@leads.csv' -F 'preset=hubspot' \
  -F 'default_country_code=GB' -F 'output_format=zip' \
  http://localhost:8000/clean/csv -o cleaned_bundle.zip
```

### `POST /demo/messy-leads`

No upload required. Accepts an optional `output_format` (`json` by default, or `csv` / `zip` — see [`/clean/csv`](#post-cleancsv)) as a form field or query parameter:

```bash
curl -X POST http://localhost:8000/demo/messy-leads
curl -X POST 'http://localhost:8000/demo/messy-leads?output_format=csv'
curl -X POST 'http://localhost:8000/demo/messy-leads?output_format=zip'
```

The endpoint reads `test_messy_leads.csv` from the repository root when present and falls back to an equivalent inline sample otherwise, so it works in minimal deployments. It runs the same cleaning pipeline as `/clean/csv` with `dedupe_by=email`, case-insensitive deduplication, date normalisation of `created_at`, and email normalisation of `email`, and returns the identical response format (including all `X-*` headers).

## Performance telemetry

Both cleaning and validation summaries include `processing_time_ms`: a non-negative float with millisecond precision, measured inside the pure service function (`clean_csv_bytes` / `validate_records`). The same value is returned on every successful HTTP response in the `X-Processing-Time-Ms` header — including CSV and ZIP downloads, where there is no JSON body:

```json
{
  "data": [],
  "summary": {
    "rows_in": 0,
    "rows_out": 0,
    "rows_dropped": 0,
    "blank_rows_skipped": 0,
    "fixes_applied": {},
    "warnings": {"malformed_rows": 0, "unknown_columns": []},
    "processing_time_ms": 0.42
  },
  "quarantined": []
}
```

The frontend displays this value as a timing badge next to the reconciliation badge.

## CI

`.github/workflows/test.yml` runs `pytest -v` on pushes and pull requests to `main` using Python 3.11.
