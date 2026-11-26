# ADR-006: Decimal + integer cents + explicit rounding

**Status:** Accepted (2026-11-26)

## Context
Contribution discrepancies are monetary. The model must never calculate or reinterpret amounts.

## Decision
- Storage: integer cents (`BIGINT`), plus a `currency` column (`USD` only in the demo).
- Domain: a `Money` value object wrapping `Decimal` and a currency. Floats are rejected at construction.
- Rounding: compute at full `Decimal` precision, then quantise **once** to cents with `ROUND_HALF_UP`.
  This is a fictional demo policy and is recorded in `rule_version`.
- The fictional demo rule: `expected_employee = eligible_pay × deferral_pct`. The employer match is
  `match_rate × min(deferral_pct, match_cap_pct) × eligible_pay`, effective-dated per plan.
- Effective-date selection: exactly one `plan_rule` must satisfy
  `effective_from ≤ pay_period.end < effective_to` (half-open). Zero rules gives `RULE_MISSING` and more
  than one gives `RULE_CONFLICT`. Both block automated drafting.
- Output: `ContributionFinding {inputs, rule_id, rule_version, formula, expected, reported, variance,
  status: MATCH|DISCREPANCY|MISSING_PAYROLL_ROW|RULE_MISSING|RULE_CONFLICT|NOT_ENROLLED, evidence_refs}`.
- **Variance policy (owner decision 2026-11-26):** variance is computed separately for the employee and
  employer components. **Any non-zero variance in either component, even 1 cent, is `DISCREPANCY`.**
  There is no tolerance band and no model involvement in deciding materiality.
- One `format_money()` function produces display strings (`$1,234.56`, negatives as `-$1.23`). The
  validator compares draft text against these strings exactly.
- The calculation function is pure and covered by table-driven and property-based tests (Hypothesis).

## Consequences
- Amounts in drafts can be verified by exact string match.
- Real plans have far more complex rules (true-ups, limits, catch-up). Those are out of scope, and the docs say so.
