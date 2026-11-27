# Decisions

The full reasoning lives in the ADRs ([docs/adr/](docs/adr/README.md)). This file is the index: what
was decided, why, and what was rejected. Owner decisions are dated.

## Architecture decision records

| ADR | Decision | Why | Rejected alternative |
|---|---|---|---|
| [001](docs/adr/001-trusted-python-core.md) | Python services are the single enforcement point; Pydantic models are the tool contract | One implementation of every policy; edge gateways are defence in depth | Enforcing only in the Cloudflare Worker (leaves the local paths unprotected) |
| [002](docs/adr/002-single-python-package-layout.md) | One package `src/retirementops/` with import-linter layers | Same boundaries as a multi-package workspace, much less overhead | The spec's `apps/` + `packages/` uv workspace |
| [003](docs/adr/003-mock-systems-as-schemas.md) | One schema per mock system, one login role per connector, RLS on all tenant tables; 404 for other tenants' objects | Least privilege and isolation that hold even if code forgets a filter | Separate mock HTTP services (heavy); one flat schema and role (no defence in depth) |
| [004](docs/adr/004-mcp-python-first-cloudflare-gateway-later.md) | Python MCP server first; the Cloudflare Worker later as a thin authenticated gateway | Offline development and testing; transport kept separate from domain logic | Worker-only MCP |
| [005](docs/adr/005-explicit-orchestrator-and-providers.md) | Explicit orchestrator; Ollama, Anthropic and Scripted providers (M3) | Readable control flow; safety testable without a live model | An agent framework from day one |
| [006](docs/adr/006-deterministic-money.md) | `Decimal`, integer cents, `ROUND_HALF_UP` once; rules evaluated on the pay period end date | Exact, auditable arithmetic | Floats; model arithmetic |
| [007](docs/adr/007-two-phase-approval.md) | Approval bound to version + hash; four-eyes for sensitive drafts; no close capability anywhere | Approval covers exactly what was reviewed and cannot be self-granted | Approval by draft ID only |
| [008](docs/adr/008-identity-and-tokens.md) | Mock RS256 JWTs locally, OAuth 2.1 at the edge (M6); roles and tenants re-read per request | Server-side authorization without real SSO; revocation takes effect at once | Trusting tenant claims inside tokens |
| [009](docs/adr/009-audit-hash-chain.md) | Append-only, hash-chained audit: tamper-evident, not immutable | Detects edits; honest about the limit | Claiming immutability |
| [010](docs/adr/010-evaluation-and-ci-gates.md) | Safety gates in CI with a scripted provider; model quality evaluated separately | Safety must not depend on model quality | One combined score |
| [011](docs/adr/011-async-deferred-to-cloudflare.md) | Synchronous local runs; Queues and Workflows in M6+ | Core workflow complete without cloud | Building async infrastructure first |
| [012](docs/adr/012-rate-limiting-and-bounded-audit.md) | Per-client and per-principal rate limits, failed-auth block, bounded anonymous audit, pseudonymised audit | An unauthenticated flood cannot fill the audit log; no raw IDs stored | Auditing every request unthrottled |
| [013](docs/adr/013-mcp-server-implementation.md) | MCP SDK 2.x low-level server with our own validation; drafts and proposals tables in M2 with DB guards; fail-closed draft status; scope-checked evidence; per-user idempotency | The spec needs extra fields rejected; M2 acceptance needs rows; integrity must not depend on code alone | `FastMCP` (ignores extra fields); app-only enforcement |

## Owner decisions

| Date | Decision |
|---|---|
| 2026-11-26 | Accept the single-package layout, Python 3.12, PostgreSQL in Docker and GitHub Actions |
| 2026-11-26 | Four-eyes approval on for financial and compliance-sensitive actions (ADR-007, implemented in M5) |
| 2026-11-26 | Database roles per connector mandatory in M1; no compromise on tenant isolation for schedule |
| 2026-11-26 | Any non-zero cent variance is a `DISCREPANCY`; no tolerance (ADR-006) |
| 2026-11-26 | Model quality kept separate from safety enforcement (ADR-010) |
| 2026-11-26 | Playbook and vendor seed data move to M7 |
| 2026-11-26 | M0 and M1 approved and committed (`ac9e36b`) |
| 2026-11-26 | M2 approved and committed (`91f959b`); ADR-013 accepted and implemented |
| 2026-11-26 | M3 approved to start |
| 2026-11-26 | Run traces get a dedicated role `svc_agent_recorder` (INSERT/SELECT on `app.agent_runs`, `app.tool_calls`, `app.evidence_items` only); `app_service` is not widened. Implemented in migration 0007: RLS on all three, trace rows only in their run's own transaction, immutable for every role. Rejected: granting `app_service` (widens the drafts role) |

## Implementation decisions inside M2 (documented in ADR-013)

- `servicekb_mock`, a provider-wide policy knowledge base with its own read role; access via
  `tickets:read`.
- Postgres trigger guards: runtime roles insert only `NEEDS_MANUAL_REVIEW` drafts and `PROPOSED`
  proposals, and only version 1 matching its header. M3 re-allows `PENDING_REVIEW`, and M5 adds a
  separate review role.
- Every guard function and `sec.tenant_scope()` pins its `search_path` (migrations 0004 and 0005).
  Trade-off: Postgres will not inline the RLS helper, a negligible cost at demo scale.
- `hide_parameters=True` on all engines.
- Forward migrations for fixes: review fixes are new revisions (0003–0005). 0001 is unchanged since
  its commit. Before M2's first commit, two already-applied, uncommitted migrations were edited:
  `0002.downgrade()` gained the `REVOKE` statements for the grants its `upgrade()` makes on `sec`
  (review Low-3), and `0003` was reformatted by ruff (whitespace only). No `upgrade()` body was
  changed in substance after being applied.

## Implementation decisions inside M3 (stages 1–3, 2026-11-26, committed in `1515ed2`)

- **Validator decides the initial status, for every client.** `DraftingService` runs the
  validator before insert. Passing → `PENDING_REVIEW`; anything else → `NEEDS_MANUAL_REVIEW` with
  value-free reason codes. A human still approves either way.
- **The database checks the validator too (0006).** `app.drafts.validator_version` is new. The
  insert guard accepts `PENDING_REVIEW` only with a validator version and an empty reasons list,
  keeps the pinned `search_path`, and the downgrade restores 0004 exactly. No grants changed.
- **Ground truth comes from the server, not the caller.** The evidence verifier now reports the
  participant, plan and pay period of each cited record from its own read. Evidence must concern
  one subject; when it identifies participant, plan and period, the finding is recomputed and
  every record it used must be cited. Money in the body must be an exact `format_money()` value
  from that finding; with no finding, no amount is allowed.
- **Run tokens (ADR-008).** A new token type, `agent_run` (10 minutes), is minted only for a
  human operator assigned to the ticket's tenant, and carries at most `tickets:read`,
  `plans:read`, `payroll:read`, `drafts:write`. Its binding (tenant, ticket, participants, plans,
  pay periods, permitted queue) is set by the host. On every request the starting operator is
  re-read, so deactivating them or removing the tenant stops the run immediately.
- **Binding enforcement.** For run principals, anything outside the binding is "not found"
  (same as another tenant), evidence outside it is refused with the same wording, ticket search
  is unavailable, and a proposal may name only the routing engine's queue (ADR-013 Low-7b). Humans
  are unaffected. Audit events of a run carry its `run_id`.
- **Routing.** Risk flags can only make the outcome more conservative. The engine reproduces
  every seeded ground-truth label (test). Confidence threshold 0.70 (configurable).

## Implementation decisions inside M3 (stages 4–5, 2026-11-26, committed in `1515ed2`)

- **Skills** carry a small, strictly parsed frontmatter: `model_tools` (read-only tools offered to
  the model; never `search_tickets` or `get_ticket_details`), `host_actions` (write tools the host
  runs itself), `run_scopes` (within the agent-run cap). The loader hashes every file in the
  skill folder; the hash and `name@version` are recorded on each run.
- **The model never gets write tools.** Drafts and proposals are created by the host after
  validation; the model only proposes text.
- **`choose_tools` returns a `ToolDecision`** (calls plus `done`), not a bare list as in the spec
  sketch, so the evidence loop ends on an explicit signal.
- **Providers:** `ScriptedProvider` parses raw script text exactly like model output (so malformed
  JSON is exercised); `OllamaProvider` uses JSON-schema structured output, temperature 0, a fixed
  seed, thinking off, a per-call timeout and a loopback-only URL (no arbitrary URL fetching). The
  optional Anthropic adapter (ADR-005) is not built in M3. Default model `qwen3:8b`, pending the
  small comparison ADR-005 asks for.

## Implementation decisions inside M3 (stage 6, 2026-11-26, committed with stage 6)

- **Host resolves, model never supplies IDs.** Tenant, participant, plan and pay period come from
  the ticket's own tenant (`TicketResolutionService`); deterministic detectors add injection,
  closure, unsupported-action, restricted-pattern, cross-tenant, ambiguity and duplicate flags.
  Two narrow connector reads were added for this (`list_plans`, `list_pay_periods`; existing
  grants, explicit tenant filter plus RLS).
- **Scope of automated drafting in M3:** contribution discrepancies only. Other intents, triage
  flags, blocking findings and solved/closed tickets end in NEEDS_MANUAL_REVIEW. Every classified
  run on an open ticket records a routing PROPOSAL (HUMAN_TRIAGE when manual review is needed).
- **Host checks on model tool calls.** Fail closed: unlisted tool, reference outside the
  resolution, oversized arguments, call/turn/model/wall-clock budgets, any tool error. Sequence is
  enforced structurally (reads only in the evidence phase, batches executed in the skill's order,
  write tools host-only after evidence is complete). A per-call prerequisite rule was tried and
  dropped: with the real model it only burned turns and added no safety.
- **The host's completeness check drives the evidence loop.** When the model stops early, the host
  reports the missing records and the exact read calls that return them (built from its own
  resolution, still checked). With this, `qwen3:8b` completed the live run reliably.
- **The model sees no values from tool results**, only which records were retrieved; amounts reach
  it solely as formatted facts from the deterministic finding (data-classification §2).
- **Kill switch** (`RO_AGENT_KILL_SWITCH`): new runs end in manual review before any model call.

## Stage 6 review fixes (2026-11-26/27, committed with stage 6)

- **Who may start a run:** a human operator assigned to the ticket's tenant, re-read from the
  directory, checked before the ticket is read and again before any model call
  (`IdentityService.require_run_operator`). Refusals raise `Forbidden`, send nothing to the model,
  write no trace and are audited as `agent_run.refused`.
- **Two kinds of run token:** the token behind model-chosen calls is read-only
  (`tickets:read`, `plans:read`, `payroll:read`). Each host action gets its own token with only
  that action's scopes (plus the read scopes a draft needs to cite evidence), valid for 120 s
  (`WRITE_TOKEN_TTL_SECONDS`; a requested lifetime can only shorten a run token, never extend it).
- **Validator content rules, as `draft-validator/2`:** links and bare web addresses, email
  addresses, phone numbers, promises of a future change to money or records ("the difference will
  be refunded"), and instruction-like or approval text each fail validation with their own
  value-free code. Ordinary service language passes ("we will update you", "our team will send
  you a summary"). Instruction text uses a dedicated draft detector that matches affirmative
  forms only, so "without review" and "cannot close this ticket" pass; the broader ticket
  detectors are not reused. *Correction:* an earlier draft of this entry kept the version at
  `draft-validator/1` as "additive"; that was wrong, because the same body could then be judged
  differently under one label. Drafts validated before this change record `/1`. A unit test pins
  each validator version to a fingerprint of its rules, so rules cannot change without a bump.
- **Skill versions are pinned to content:** `service-ticket-triage` is `1.1.0` (its text changed in
  stage 6); a unit test pins each `name@version` to its content hash, so text cannot change again
  without a version bump.
- **Trace failures are visible:** `agent_run.finished` records `trace_written`, a failed write adds
  the reason `TRACE_NOT_WRITTEN` and emits `agent_run.trace_failed`.
- **Guards:** a non-blocking lock is taken at the very start of `run()` and released only after
  the run, proposal, trace and audit have finished, so a second run on the same instance (another
  thread, or re-entrant) raises immediately. Genuine two-thread tests hold run A at the ticket
  read, the model call and the trace write; they fail if the lock is removed. Non-scalar tool
  arguments are refused as `host_refused_malformed_arguments`.

## Awaiting approval

- Nothing for M2. M2 was approved and committed as `91f959b` (2026-11-26). ADR-013 is accepted
  and implemented, and migrations 0002–0005 are committed. Open owner decisions (pushing to
  GitHub, starting M3) are tracked in [HANDOFF.md](HANDOFF.md).
