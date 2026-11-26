# ADR-007: Two-phase approval bound to version + hash; four-eyes for sensitive actions; no close capability

**Status:** Accepted (2026-11-26). Amended the same day: four-eyes approval is **on** for financial and compliance-sensitive actions.

## Context
The agent proposes and humans approve. Approval must cover exactly what was reviewed, and must not be
replayable or self-grantable. The project owner requires editor and approver to be different people for
financial and compliance-sensitive actions.

## Decision
- Draft versions are immutable rows: `(draft_id, version, body, evidence_refs, content_hash, author_principal, created_at)`.
  `content_hash = sha256(canonical_json(body, evidence_refs, target_ticket_id, action))`.
- The approve request must include `version` and `content_hash`. The server runs the checks in
  architecture.md §4. A mismatch returns `409`.
- The execution service is a separate application service. It is called only from the approval
  endpoint, inside the same request, after the checks pass. It has no route of its own that an agent
  token could reach. It uses the `svc_zendesk_writer` connector identity.
- Idempotency: unique constraint on `(draft_id, version, action)` in `approvals`. A retry returns the
  stored outcome.

### Four-eyes rule
A draft is **sensitive** if any of the following holds. The host decides this, never the model.
- the ticket intent is `CONTRIBUTION_DISCREPANCY`, `PAYROLL_FILE_ISSUE` or `ELIGIBILITY_QUESTION`;
- the evidence bundle contains a `ContributionFinding`, or the body contains any monetary or percentage token;
- it is a compliance artifact (questionnaire answer or vendor exception);
- it is an assignment proposal attached to a sensitive draft.

For a sensitive draft, the approver must **not** be:
1. the author of the version being approved, or
2. any human who authored any earlier version of the same draft.

The agent principal can never approve anything (see ADR-008). Violations return `403` with reason
`FOUR_EYES_REQUIRED` and are audited. The rule is a pure domain function
(`domain/approval_policy.py`) with unit tests, built in M5. The config flag
`FOUR_EYES_SENSITIVE=true` is the default, and turning it off requires an admin and is audited. Non-sensitive drafts (for
example `ACCESS_OR_PORTAL_ISSUE`) may be edited and approved by the same reviewer.

### Closure
There is no close or solve capability anywhere in RetirementOps: no MCP tool, API route, connector
method or DB grant. Tests:
  1. The tool registry contains no name matching `/close|solve|delete|send|transfer|modify_enrollment|change_contribution/` (M2).
  2. No connector interface has a status-mutation method (M1).
  3. Crafted API requests that try to set a status (for example `PATCH /tickets/{id}` with `status=solved`) are rejected (M1; extended to approval routes in M5).
  4. A raw `UPDATE ... SET status` from `svc_zendesk_writer` fails on both the column grant and the trigger (M1).

The monitoring metric `unresolved_ticket_closed_count` must be 0. It counts tickets in solved or closed
status with open blockers or pending drafts.

## Consequences
- The demo can show exactly why a stale or edited draft cannot be approved by mistake.
- Demoing a sensitive approval needs two reviewer identities (`rev.sam` edits, `rev.lee` approves).
- Real closure stays a human action in the (mock) ticketing system.
