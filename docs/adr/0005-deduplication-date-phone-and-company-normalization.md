# ADR-0005: Deduplication, date, phone and company normalization

- **Status:** Accepted
- **Date:** 2026-11-21

## Context

Dirty CRM exports contain case-variant IDs, missing keys, ambiguous numeric
dates, phones in many national formats, and company names with trailing legal
suffixes or casing variants. Early dedupe lowercased every key (breaking
case-sensitive Salesforce IDs), collapsed blank keys into one row, and parsed
ambiguous dates per cell, so one column could mix day-first and month-first
interpretations.

## Decision

**Deduplication** (`dedupe`) keeps the first row per trimmed key. Case folding
only happens when `dedupe_ignore_case=True`; the default is case-sensitive.
Blank/whitespace-only keys become null and are preserved by
`key.is_null() | key.is_first_distinct()`, with the count reported as
`rows_missing_dedupe_key`. When `fuzzy_dedupe=True`, keys are produced by
`fuzzy_entity_key` so entity variants (for example `ACME, Inc.` and
`Acme Corp`) collapse onto the first row while null keys remain untouched.

**Dates** (`normalize_dates`) select one format per ambiguous numeric pair from
evidence in the whole column (the format parsing the most non-null cells wins;
`date_dayfirst` breaks ties). Structurally unambiguous formats remain available
per cell. Unparseable values are preserved verbatim and counted in
`unparseable_dates`, while cells matching both readings are counted in
`ambiguous_dates`.

**Phones** use the pinned `phonenumbers` library. `normalize_phone_e164(value,
default_region)` returns `(e164, was_changed, error)`: blank/null values are
left alone, valid numbers are formatted as E.164, and invalid/unparseable
numbers yield a region-specific error. `CleanOptions.default_country_code` is a
two-letter code uppercased by a validator. `normalize_phones` runs on the
freshly parsed frame (before other transforms) so quarantined records retain
raw values and correct 0-based source row indices; changed cells are counted in
`phones_normalized`.

**Companies** use a standard-library normalizer (`app/utils/companies.py`, no
new dependency). `normalize_company_name` collapses whitespace, strips trailing
legal suffixes (`Inc`, `LLC`, `Ltd`, `Corp`, `GmbH`, …), title-cases all-lower or
all-upper multi-word names, but preserves short uppercase acronyms such as
`IBM` and `AWS` (`_is_acronym`). `normalize_companies` applies it via
`map_elements` and counts changed cells in `companies_normalized`.
`fuzzy_entity_key` additionally strips punctuation, removes suffixes, and
lowercases to produce a comparable dedupe key.

## Consequences

**Positive**

- Case-sensitive IDs and blank keys are no longer lost; date interpretation is
  deterministic per column; phones and company variants can be normalized or
  quarantined on demand.
- CRM presets (ADR-0006) can opt into these transforms using known headers
  without changing the underlying pipeline.

**Trade-offs**

- Phone normalization adds a third-party dependency (`phonenumbers`) and
  quarantines invalid rows, so callers must inspect `quarantined`.
- Company normalization is intentionally conservative: it removes legal
  suffixes and fixes casing only, without external entity-resolution data.
  Fuzzy dedupe is a key-equivalence heuristic, not a guarantee of identity.
- `fuzzy_dedupe` and company normalization use `map_elements` (a Python call per
  cell); they are opt-in so the default path stays vectorized.

## Code references

`app/services/csv_cleaner.py` (`dedupe`, `normalize_dates`, `normalize_phones`,
`normalize_companies`, `clean_csv_bytes`), `app/utils/companies.py`,
`app/utils/phones.py`, `app/utils/dates.py`, `app/schemas/clean.py`,
`tests/services/test_csv_cleaner.py`, `tests/utils/test_companies.py`,
`tests/utils/test_phones.py`, `AUDIT_FIXES.md` items 1, 2, 7.
