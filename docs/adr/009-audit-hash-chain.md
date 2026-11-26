# ADR-009: Append-only audit with hash chain: tamper-evident, not immutable

**Status:** Accepted (2026-11-26)

## Decision
- `audit_events` rows: `seq` (monotonic), `occurred_at`, `actor_principal`, `actor_role`, `tenant_id`,
  `correlation_id`, `event_type`, `target_ref`, `details` (redacted JSON), `prev_hash`, `hash`.
  `hash = sha256(prev_hash || canonical_json(row_without_hash))`.
- The application DB role has `INSERT` and `SELECT` only on `audit_events`, with no `UPDATE` or `DELETE`.
  A trigger blocks updates as well.
- `governance.audit.verify_chain` recomputes the chain and reports the first break. A CLI wrapper is planned for M8.
- **Privacy (ADR-012):** identifiers are pseudonymised before writing, and `append` refuses raw participant refs, employee refs or SSN-like values. Paths are stored as route templates.
- **Volume (ADR-012):** anonymous and rate-limit events are throttled, so the table cannot be flooded.

## Honest limitation
A database superuser can rewrite rows and recompute the chain. This design **detects** edits by anyone
who cannot also rewrite every later hash. It does not prevent them. Real immutability would need
external anchoring, for example periodically publishing the chain head to a write-once store. That is
documented as future work. **The project must not describe the audit log as immutable.**
