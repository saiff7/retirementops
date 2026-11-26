# ADR-011: Synchronous local runs; Queues and Workflows come in M6+

**Status:** Accepted (2026-11-26)

## Decision
- M1 to M5: an operator starts a run on one ticket. It executes in the API process with budgets, and
  bulk ingest is a script. Waiting for approval is just persisted state (`PENDING_REVIEW`). No process
  waits.
- M6+: Cloudflare Queues for bulk ticket fan-out, and a Cloudflare Workflow that durably waits for an
  approval event and then calls the approval/execution API. The Workflow holds no business truth. It
  references `draft_id` and `version`.
- Durable Objects are not used unless a concrete per-ticket concurrency need appears.
  Postgres row locks plus idempotency keys cover the current needs.

## Consequences
The core workflow is complete and testable without Cloudflare, and the Cloudflare pieces are additive.
