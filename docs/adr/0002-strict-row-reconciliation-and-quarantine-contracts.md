# ADR-0002: Strict row reconciliation and quarantine contracts

- **Status:** Accepted
- **Date:** 2026-11-21

## Context

An earlier implementation derived `rows_in` from the Polars frame, which made
the summary internally consistent by construction but wrong in practice: blank
lines became all-null output rows, and rows dropped or invented by Polars could
not be detected. The two endpoints also disagreed on whether quarantined rows
counted in `rows_dropped`, and ignored column names were discarded in JSON mode.

## Decision

Adopt one explicit, testable accounting contract for both endpoints:

```
rows_in == rows_out + rows_dropped + len(quarantined)
```

On `/validate/json` the quarantined term is `quarantined_count`.

- `rows_in` is counted from `csv.reader` **input records**, not from the frame.
  Blank records are skipped and counted in the top-level `blank_rows_skipped`
  field of `SummaryReport`; ragged records are quarantined with a reason.
- `rows_dropped` counts blank rows and rows removed by transforms (such as
  deduplication) but **never** quarantined rows. Invalid-phone rows are removed
  from the frame and quarantined, so `len(phone_quarantines)` is explicitly
  subtracted from the drop calculation to avoid double counting.
- `fixes_applied` remains `dict[str, int]` with stable base keys
  (`emails_normalized`, `dates_normalized`, `unparseable_dates`,
  `duplicates_removed`) and conditional keys (`phones_normalized`,
  `companies_normalized`, `ambiguous_dates`, `rows_missing_dedupe_key`) emitted
  only when the relevant option was used or condition occurred.
- Non-fatal data-quality signals live in the shared `WarningSummary`:
  `malformed_rows` and `unknown_columns`. Ignored option column **names** are
  preserved there (surfaced as the `X-Unknown-Columns-Ignored` header in CSV
  mode) rather than being reduced to a count.
- `QuarantinedRecord` (`row_index`, `record`, `errors`) is defined once in
  `app/schemas/common.py` and re-exported by `app/schemas/validate.py`. For CSV
  rows, `row_index` is the 0-based index among raw data rows (header excluded),
  and `record` is the raw field list; JSON validation records carry the original
  object or value.
- `ValidateSummary.rows_dropped` is always `0`: quarantines are reported
  separately through `quarantined_count` and the `quarantined` list.

## Consequences

**Positive**

- The invariant is enforced by `read_csv_with_report`'s shape/row-count checks
  and asserted directly in router and service tests, including cases mixing
  blank, malformed, phone-invalid, and deduplicated rows.
- Callers can always recover rejected rows and their reasons from JSON mode or
  the ZIP exports; nothing is silently dropped.

**Trade-offs**

- Counters must be updated in lockstep whenever a new transform can remove or
  quarantine rows; this is why the drop calculation explicitly names
  `phone_quarantines`.
- `warnings.malformed_rows` currently equals the total quarantine count, so it
  includes invalid phones as well as structurally ragged CSV rows.

## Code references

`app/schemas/common.py` (`WarningSummary`, `SummaryReport`, `QuarantinedRecord`),
`app/schemas/validate.py` (`ValidateSummary`),
`app/services/csv_cleaner.py` (`_filter_malformed_rows`, `read_csv_with_report`,
`normalize_phones`, `clean_csv_bytes`),
`app/services/json_validator.py` (`validate_records`), `tests/routers/`,
`tests/services/`.
