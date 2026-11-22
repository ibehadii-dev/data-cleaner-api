# ADR-0004: Validation hardening and CSV formula-injection defense

- **Status:** Accepted
- **Date:** 2026-11-21

## Context

`/validate/json` accepted unbounded batches, used a quadratic email regex on
arbitrarily long strings, called `float()` on huge integers (raising
`OverflowError` → HTTP 500), ignored `FieldRule.nullable`, and accepted strings
such as `"1_000"` and `"1e3"` as numbers. Its date parser also accepted ISO
offsets and fractional seconds that the CSV cleaner considered unparseable.
Separately, CSV export cells beginning with `=`, `+`, `-`, or `@` could execute
as formulas when opened in a spreadsheet.

## Decision

- Bound input: `ValidateRequest.records` uses `Field(max_length=1000)`; the app
  middleware caps the request body at 2 MB (413 on overflow).
- Keep email validation safe: trim the value, reject strings longer than 254
  characters **before** running the regex, then apply the strict address regex.
- Keep numeric validation strict and non-coercing: strings must fully match
  `^[+-]?\d+(\.\d+)?$`. Booleans are never numbers. Existing `int` values are
  returned as-is (avoiding `OverflowError` from `float()`), other values go
  through guarded conversion with `math.isfinite` and
  `except (ValueError, OverflowError, TypeError)`. Validated records are
  returned unchanged.
- Honor `FieldRule.nullable`: a `null` (including an absent field) satisfies a
  required field when `nullable=True`; blank strings still fail.
- Share one date-format list (`DATE_FORMATS` in `app/utils/dates.py`) between
  cleaner and validator. The `fromisoformat` fallback was removed, so offset
  and fractional-second timestamps are unsupported by both endpoints.
- Run validation in a threadpool: `validate_json` is a plain `def`, and the
  cleaner is called via `run_in_threadpool`, so CPU work cannot block `/health`.
- Escape formula prefixes in **CSV output only**: `_FORMULA_PREFIX_RE =
  r"^[=+\-@]"`. `render_csv` prefixes matching cells with a single quote using
  Polars expressions; `render_bundle_quarantined_csv` applies the same rule via
  `_escape_formula_cell` to individually written cells. JSON records keep their
  original values.

## Consequences

**Positive**

- Malicious or accidental inputs now produce 413/422 responses or quarantined
  records instead of 500s or multi-second regex stalls.
- Cleaner and validator agree on what counts as a date; spreadsheet exports are
  safe while JSON data fidelity is preserved.

**Trade-offs**

- Numeric strings are validated but never converted; callers wanting numbers
  must parse them themselves.
- Unsupported ISO variants are intentionally rejected in both endpoints rather
  than accepted in one; this is documented in `HANDOFF.md`.

## Code references

`app/schemas/validate.py` (`ValidateRequest`, `FieldRule`),
`app/services/json_validator.py` (`_valid_email`, `_check_type`,
`validate_records`), `app/utils/dates.py` (`DATE_FORMATS`, `parse_date_string`),
`app/services/csv_cleaner.py` (`_FORMULA_PREFIX_RE`, `render_csv`,
`render_bundle_quarantined_csv`), `app/main.py` (body-size middleware),
`AUDIT_FIXES.md` items 4, 8, 9, 14, 15, 16, 20.
