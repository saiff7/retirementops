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
| [005](docs/adr/005-explicit-orchestrator-and-providers.md) | Explicit orchestrator; Ollama, Anthropic and Scripted providers (M3). *Amendment proposed 2026-11-27, pending approval:* the model comparison moves to M4; `qwen3:8b` stays the default | Readable control flow; safety testable without a live model | An agent framework from day one |
| [006](docs/adr/006-deterministic-money.md) | `Decimal`, integer cents, `ROUND_HALF_UP` once; rules evaluated on the pay period end date | Exact, auditable arithmetic | Floats; model arithmetic |
| [007](docs/adr/007-two-phase-approval.md) | Approval bound to version + hash; four-eyes for sensitive drafts; no close capability anywhere | Approval covers exactly what was reviewed and cannot be self-granted | Approval by draft ID only |
| [008](docs/adr/008-identity-and-tokens.md) | Mock RS256 JWTs locally, OAuth 2.1 at the edge (M6); roles and tenants re-read per request | Server-side authorization without real SSO; revocation takes effect at once | Trusting tenant claims inside tokens |
| [009](docs/adr/009-audit-hash-chain.md) | Append-only, hash-chained audit: tamper-evident, not immutable | Detects edits; honest about the limit | Claiming immutability |
| [010](docs/adr/010-evaluation-and-ci-gates.md) | Safety gates in CI with a scripted provider; model quality evaluated separately. *Amendment proposed 2026-11-27, pending approval:* the M4 evaluation framework (database, datasets, entry point, package, scoring, gates, reports) | Safety must not depend on model quality | One combined score |
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
| 2026-11-27 | M3 complete (stages 1–7 in `1515ed2`, `d7a9a63`, `3ab1238`) |
| 2026-11-27 | M4 architectural decisions approved; Stage 0 (recording them) only. Stages 1–8 each need approval before they start. The recorded text ("M4 decisions" below, ADR-010 Amendment 1, ADR-005 Amendment 1) is **pending approval** |
| 2026-11-27 | `ro_owner` must not be used for scoring; scoring uses a separate, narrowly scoped read-only role (design in ADR-010 Amendment 1, point 1a, pending approval) |
| 2026-11-27 | The snapshot blind spot (writes under a tenant outside the scoring reader's scope) is accepted as a documented risk, with no post-run owner check (ACC-15) |
| 2026-11-27 | The credential-separation fix (OPEN-11) is limited to the evaluation process; extending it to the API, MCP server or worker needs separate owner approval |

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

## Implementation decisions inside M3 (stage 7, 2026-11-27, committed in `3ab1238`)

- **Developer skills live in `.claude/skills/<name>/SKILL.md`** (Claude Code format: `name` and
  `description` frontmatter). They are instructions for working on this repository, separate from
  the runtime skills in `skills/`, and point to `CLAUDE.md` rather than repeating it.
- **`write-eval` documents the current harness only.** It describes safety-invariant tests with
  `ScriptedProvider` and labelled cases from the seed, and states that the M4 runner, golden
  files and reports are not built yet (ADR-010 unchanged).
- **`security-review` keeps the planned name.** Inside this project it provides the
  RetirementOps checklist in place of Claude Code's generic review of the same name.

## M4 decisions (Stage 0, recorded 2026-11-27; pending owner approval)

Recorded before any M4 code exists. The owner approved the architectural decisions on 2026-11-27;
this record of them, including the scoring-access design and the security-review corrections, is
**pending the owner's approval**. The full reasoning is in
[ADR-010 Amendment 1](docs/adr/010-evaluation-and-ci-gates.md) and
[ADR-005 Amendment 1](docs/adr/005-explicit-orchestrator-and-providers.md).

| Decision | Why | Rejected alternative |
|---|---|---|
| A dedicated evaluation database, `retirementops_eval`, rebuilt from seed 42 for each run and hash-checked. Provisioning refuses any other database name. | Reproducible runs; avoids the shared test database (OPEN-4); never touches the dev database (still at 0005) | Reusing the test or dev database; SQLite |
| Setup uses two privileged identities, only in `evaluations.provision`, only for `retirementops_eval`. The **superuser** (existing `db.bootstrap`) creates roles, drops and creates the database, revokes `PUBLIC`, grants CONNECT and sets the reader's read-only default. The superuser step also refreshes every project role cluster-wide (idempotent). **`ro_owner`** migrates, loads the seed and inline tickets, checks the hash and grants the reader's schema, table and function privileges. Stage 2 makes the two privileged settings optional, adds a run-and-score settings mode that excludes them even when `.env` holds them, and makes the entry points that need them fail clearly without them. Stage 3 runs provisioning in its own process. The API, MCP server and worker are unchanged (OPEN-11). | The system under test keeps its runtime roles; only setup needs privileged rights, and a process that never holds the secrets cannot leak them | Using `ro_owner` for scoring too (owner, 2026-11-27: too broad); one process holding every secret (today's behaviour, OPEN-11) |
| Scoring reads use a new login, `svc_eval_reader`, from one module (`evaluations.observe`) only. It has SELECT only, in `retirementops_eval` only, on the four mock schemas plus the drafts, proposals and trace tables. It cannot read the audit or users tables, has no write privileges, and RLS still applies (NOBYPASSRLS; the tenant scope is the golden manifest's tenant list). It is not in `RUNTIME_ROLES`, so it has no CONNECT on the dev or test databases and no runtime engine. `PUBLIC` has no CONNECT on the eval database; only the superuser, `ro_owner`, the eight runtime roles and the reader can connect. Its effective function rights are `sec.tenant_scope()` plus the trigger functions every role gets through PostgreSQL's default `PUBLIC` EXECUTE (OPEN-12). Its grants come from provisioning, not a migration. Its password is loaded by every process (extends OPEN-11). Writes under a tenant outside its scope would not show in snapshots (accepted, ACC-15). It departs from three CLAUDE.md database rules, which Stage 2 notes in CLAUDE.md. Its exact privilege set is tested. | No existing role is both read-only and able to see every schema scoring needs: mock readers are one schema each, and `app_service` and `svc_agent_recorder` can insert | `ro_owner` for scoring; combining runtime roles (adds write rights); a migration (puts eval grants in every database) |
| The eval runner uses an explicit evaluation rate-limit configuration; defaults are unchanged | A fast scripted run would otherwise hit the shared burst of 120 and fail cases for the wrong reason | Raising the production defaults |
| Golden cases live only in `data/golden/{dev,heldout}/`, with `data/golden/manifest.json` (version, case hashes, seed dataset hash, tenant list). The first set is 30 cases, about 20 dev and 10 held-out. | One location; the case set is versioned and tamper-evident | The spec's `skills/service-ticket-triage/eval-cases.jsonl`; `evals/cases/` |
| Expectations come from the seed's ground truth, reviewed and then frozen | The system cannot grade itself | Labels taken from validator, routing or model output |
| The entry point is `scripts/run_eval.py` (`--tier safety`, `--tier quality`) | The repository has no Makefile | `make eval` |
| The package is `src/retirementops/evaluations/` (`dataset`, `provision`, `observe`, `recording`, `runner`, `scorers`, `gates`, `report`), with a new import contract: above `agent_runtime`; never imports `connectors` or `store`; only `provision` and `observe` may use `db`; only `provision` may use `seed`. Source tests check which role each module uses. Scorers open no connection. | Keeps the evaluation on the real stack without becoming a backdoor around it | Scoring inside the test suite only |
| A recording wrapper around the provider captures first-pass choices, proposed arguments, parse failures (retries included) and latency. The orchestrator is not changed. | Traces store only argument hashes, and the orchestrator retries silently | Changing the orchestrator for evaluation |
| Safety tier (`ScriptedProvider`, CI, blocks) and quality tier (Ollama, local, reported) stay separate | Safety does not depend on the model (owner decision of 2026-11-26, unchanged) | One combined score |
| Scoring uses frozen golden expectations only. Tool accuracy is scored on first-pass choices. A missing trace counts as a failure. | Prevents circular scoring, credit for host help and silent skips | Scoring against the validator or routing engine |
| Metrics follow spec §12. Review-queue age and approval/rejection rates are **N/A until M5**. "Duplicate execution = 0" means no duplicate open proposals and no execution path until M5. | Honest about what M3 can measure | Estimating M5 metrics |
| "Schema validity 100%" is a safety gate on **accepted** outputs. The raw parse-failure rate is a quality metric. | Retries would otherwise hide failures, or the gate could never pass | One undefined number |
| Every safety gate gets a "gate trips" test that sabotages its defence | Proves each gate can fail | Trusting a green report |
| Quality targets are unchanged: macro-F1 ≥ 0.90 (held-out) and routing ≥ 0.90. A miss shows "NOT MET" and is never lowered. | Spec §12 | Lowering targets to pass |
| Full run metadata is recorded (ADR-010 Amendment 1, point 10), and a run with incomplete metadata aborts | Every result must be traceable | Partial metadata |
| "Reproducible" means recorded inputs and metadata plus repeats (default 3) with the variance reported. Scripted runs are byte-identical apart from timestamps and IDs. | Local models are not strictly deterministic | Claiming determinism |
| Reports go in `evals/reports/<run-id>/` (`results.jsonl`, `summary.json`, `report.md`). They hold no ticket or participant text, only case IDs. They are compared with a committed baseline, and regressions are flagged. | Spec §12: compare releases and keep regressions visible; privacy hygiene | Reports with ticket excerpts; no baseline |
| `qwen3:8b` stays the default. The 8B vs 14B comparison runs in M4 on **dev** with repeats; **held-out** runs once per model for the final evaluation. The owner decides any change. | The M3 comparison was never run; a smoke run is not an evaluation | Switching now; backfilling an M3 result |

### M4 stages (structure approved 2026-11-27; this text pending approval)

Each stage starts only with the owner's approval and ends with real test output
(CLAUDE.md ground rule 1). The plan is in [docs/implementation-plan.md](docs/implementation-plan.md).

| Stage | Scope |
|---|---|
| 0 | Decisions and ADR amendments (documentation only). **This record.** |
| 1 | Golden dataset (30 cases, dev/held-out), strict case schema, manifest with tenant list, integrity tests, `evaluations` import contract |
| 2 | `retirementops_eval` provisioning with name guard, seed and hash check, eval rate-limit config, isolation tests; the `svc_eval_reader` role and its grants, with exact-privilege tests; optional privileged settings with a run-and-score mode that excludes them; the CLAUDE.md note on three exceptions |
| 3 | Recording wrapper, `observe` (readback and snapshots as `svc_eval_reader`), runner, per-case results, full run metadata, `scripts/run_eval.py`; process separation and the reader code-boundary test |
| 4 | Deterministic scorers for the spec §12 metrics (N/A for M5-dependent ones) |
| 5 | Safety tier: adversarial and injection matrix, ADR-010 gates, "gate trips" tests |
| 6 | Quality tier: `qwen3:8b` vs `qwen3:14b` on dev with repeats, then held-out once; written comparison for the owner |
| 7 | Reports, baseline comparison and regression flags; commit a local Ollama report |
| 8 | CI integration and a deliberate-break demonstration (needs a push, OPEN-1) |

## Awaiting approval

- Approving and committing the recorded M4 Stage 0 text (uncommitted, 2026-11-27); then starting
  M4 Stage 1.
- Pushing `main` to GitHub (OPEN-1), needed for M4 acceptance (Stage 8).
- Upgrading the dev database from 0005 to 0007. M4 does not need it.
- Any change to the default model after the Stage 6 comparison.
- When Stage 2 lands: the CLAUDE.md note covering the three exceptions (the reader's grants outside
  migrations; `ro_owner` and the superuser in `evaluations.provision`; one non-runtime role reading
  all four mock schemas in `retirementops_eval` only). See ADR-010 Amendment 1, point 1a.
- Extending the credential-separation fix (OPEN-11) to the API, MCP server or worker.
