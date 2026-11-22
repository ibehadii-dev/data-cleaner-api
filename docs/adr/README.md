# Architecture Decision Records

This directory holds the architectural decisions behind `data-cleaner-api`.
Each ADR is a short, immutable snapshot of the context, the decision, and its
consequences at the time the decision was made.

| ID | Title | Status | Date | Link |
| --- | --- | --- | --- | --- |
| ADR-0001 | FastAPI + Polars + Pydantic v2 stack | Accepted | 2026-11-21 | [0001-fastapi-polars-pydantic-v2-stack.md](0001-fastapi-polars-pydantic-v2-stack.md) |
| ADR-0002 | Strict row reconciliation and quarantine contracts | Accepted | 2026-11-21 | [0002-strict-row-reconciliation-and-quarantine-contracts.md](0002-strict-row-reconciliation-and-quarantine-contracts.md) |
| ADR-0003 | CSV ingestion, encoding and delimiter guardrails | Accepted | 2026-11-21 | [0003-csv-ingestion-encoding-and-delimiter-guardrails.md](0003-csv-ingestion-encoding-and-delimiter-guardrails.md) |
| ADR-0004 | Validation hardening and CSV formula-injection defense | Accepted | 2026-11-21 | [0004-validation-hardening-and-csv-formula-injection-defense.md](0004-validation-hardening-and-csv-formula-injection-defense.md) |
| ADR-0005 | Deduplication, date, phone and company normalization | Accepted | 2026-11-21 | [0005-deduplication-date-phone-and-company-normalization.md](0005-deduplication-date-phone-and-company-normalization.md) |
| ADR-0006 | CRM presets, multi-format exports, telemetry, UI and CI | Accepted | 2026-11-21 | [0006-crm-presets-multi-format-exports-telemetry-and-ui.md](0006-crm-presets-multi-format-exports-telemetry-and-ui.md) |

## Guidelines for new ADRs

- Use the next sequential number (`0007-…`) and a kebab-case filename. Record an
  ISO date (`YYYY-MM-DD`) and one of the statuses `Proposed`, `Accepted`, or
  `Superseded`.
- Keep one decision per ADR: record the **context**, the **decision**, and the
  **consequences** (including trade-offs), with concrete code references that
  can be verified against the repository.
- **Do not rewrite history.** If a later decision changes an earlier one, add a
  new ADR that supersedes it; change the old ADR's status to
  `Superseded by [ADR-XXXX](XXXX-….md)` and keep its original body intact.
- Update this index in the same change as the new ADR.
- ADR-only changes must not alter application business logic or existing test
  assertions. They document decisions; they do not make them.
- Read the relevant ADRs before modifying schemas, reconciliation rules,
  validation logic, or the cleaning pipeline (see `AGENTS.md`).
