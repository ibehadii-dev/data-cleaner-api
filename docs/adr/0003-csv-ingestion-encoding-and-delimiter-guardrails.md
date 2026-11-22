# ADR-0003: CSV ingestion, encoding and delimiter guardrails

- **Status:** Accepted
- **Date:** 2026-11-21

## Context

Real exports arrive as UTF-8 with or without BOM, Windows-1252, or Latin-1, and
frequently use `;`, tab, or `|` separators. Previously a semicolon file parsed
as a single column with HTTP 200, duplicate headers were silently renamed by
Polars, header whitespace defeated column options, and a 24 MB upload could
peak at roughly 20x its size in memory.

## Decision

Harden ingestion at three layers.

**Decoding** (`decode_csv_bytes`) tries `utf-8-sig` (stripping an optional BOM),
then `cp1252` (so smart quotes decode correctly), then `latin-1` as a last
resort. Embedded NUL bytes are rejected as binary content. Empty or
BOM-only input yields an empty frame. All failures raise `CsvReadError`, mapped
to HTTP 400 in `app/main.py`.

**Record scanning** (`_filter_malformed_rows`) streams one `csv.reader` record at
a time into a clean text buffer:

- Delimiter is explicit when supplied, otherwise sniffed over `,`, `;`, tab,
  and `|` by choosing the candidate that yields the most header fields (ties
  prefer the earlier candidate). A `delimiter` option validator also accepts a
  literal `\t` from forms.
- A one-column header containing `;` or a tab is rejected with HTTP 400 rather
  than being misparsed as a single ignored column.
- Duplicate headers after whitespace stripping are rejected with HTTP 400.
- Header whitespace is trimmed so `email`, `date_columns`, and dedupe options
  match their columns.
- Blank rows are skipped and counted; ragged rows are quarantined with
  `"malformed row: expected N columns, found M"`.

**Parsing and limits**: Polars reads with `infer_schema_length=0` (all columns as
`String`) and `truncate_ragged_lines=True` as a defensive backstop. Frame width
and height are reconciled against the header and raw record counts, raising
`CsvReadError` on mismatch. Uploads are read in 64 KB chunks with a cumulative
25 MB cap and a `file.size` pre-check; middleware rejects oversized requests by
`Content-Length` (2 MB for `/validate/json`, 25 MB + multipart overhead for
`/clean/csv`) with HTTP 413. Intermediates are deleted and JSON is emitted via
`frame.write_json()` to bound peak memory.

## Consequences

**Positive**

- Wrong delimiters, duplicate headers, and unreadable files fail fast with clear
  400s instead of producing plausible-looking but empty results.
- Memory scales with the upload rather than with a full Python list of rows;
  `/health` remains responsive during large cleans.

**Trade-offs / known limitation**

- The middleware size guard is header-based; a client can omit
  `Content-Length`. The chunked streaming cap and `file.size` check still bound
  handler memory, but the real network limit should also be enforced at the
  proxy. This limitation is recorded in `HANDOFF.md`.
- Delimiter sniffing is header-based, so unusual files may still need the
  explicit `delimiter` option.

## Code references

`app/services/csv_cleaner.py` (`decode_csv_bytes`, `_detect_delimiter`,
`_filter_malformed_rows`, `read_csv_with_report`, `render_csv`),
`app/schemas/clean.py` (`CleanOptions.normalize_delimiter`),
`app/routers/clean.py` (`read_upload_limited`),
`app/main.py` (`enforce_body_size_limit`, `CsvReadError` handler),
`AUDIT_FIXES.md` items 3, 5, 6, 10, 11, 12, 19.
