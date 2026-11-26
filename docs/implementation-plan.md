# Implementation Plan

Build one milestone at a time. Each milestone ends with: tests run (with real output), a summary of
changed files, known limitations, and the owner's review before the next milestone starts.

## Owner decisions (2026-11-26)

| # | Question | Decision |
|---|---|---|
| Q1 | Layout | Single package `src/retirementops/` with import-linter (ADR-002) |
| Q2 | Python version | 3.12, uv-managed |
| Q3 | Test database | PostgreSQL 16 in Docker (host port 55432) for integration tests. Pure domain tests need no DB. |
| Q4 | Ollama model | Decided in M3 by a small dev-set bake-off. The digest is recorded. |
| Q5 | Four-eyes | **On** for financial and compliance-sensitive actions. Editor and approver are separated (ADR-007). Implemented in M5. |
| Q6 | Variance tolerance | **None.** Any non-zero cent variance in either component is `DISCREPANCY` (ADR-006). |
| Q7 | DB roles per connector | **Mandatory in M1**, plus row-level security (ADR-003). No compromise on tenant isolation for schedule. |
| Q8 | CI | GitHub Actions. Safety gates use `ScriptedProvider`. Model quality is kept separate from safety enforcement (ADR-010). |

## Milestones

### M0: Discovery and design ✅ (approved 2026-11-26)
Docs only: business process, as-is/to-be, source of truth, data classification, threat model,
architecture, ADRs, and this plan.

### M1: Synthetic business backend ✅ (approved 2026-11-26)

**Delivered as planned, with these notes:**
- Migrations, roles per connector, RLS, the column grant plus trigger on `tickets.status`, the append-only audit trigger, and a seed that round-trips byte-for-byte (`dataset_hash` in `data/synthetic/manifest.json`).
- Read API: `GET /tickets`, `/tickets/{id}`, `/plans/{id}`, `/plans/{id}/rules/applicable`, `.../enrollment`, `.../contributions`, `.../contribution-finding`. There are no write routes. `POST /auth/dev-token` exists only when `RETIREMENTOPS_ENV=dev`.
- Payroll upload validation and ingestion exist as a service (`PayrollIngestService`) behind the new `payroll:ingest` scope, which operators hold. There is no HTTP route yet. The UI upload comes in M5.
- `ZendeskMockWriter` (post reply, apply assignment) has no caller until the M5 execution service. Its success, idempotent-replay and stale-version paths are tested (`tests/integration/test_connector_paths.py`).
- **Review fixes (2026-11-26):** rate limiting and bounded anonymous audit (ADR-012), pseudonymised audit records with route templates, UUID-only correlation IDs, `api/main.py` as an app factory with lifespan engine disposal, tests for the writer success paths and the PlanAdmin lookups, and the routing table documented (business-process §2.3a).
- Ticket count is 121, inside the spec's 100–150 range. Each deliberate payroll case also gets a matching ticket.

**Spec items placed in later milestones (approved by the owner, 2026-11-26):**

| Spec item | Proposed milestone | Why not in M1 |
|---|---|---|
| §9 seed: 30–50 approved-answer passages with version, section, effective date and citation metadata (`documents`, `document_passages`) | **M7** | The passage schema has to match the citation format, expiry rules and retrieval tools designed in M7. Seeding it now would fix a schema before its only consumer exists, and likely mean a migration rewrite. |
| §9 seed: 20–30 vendors across risk tiers, with findings (`vendors`, `vendor_findings`) | **M7** | Same reason: the deterministic vendor checks (overdue review, missing SOC report, stale evidence) set which fields and dates the seed needs. |
| §9 tables `agent_runs`, `tool_calls`, `evidence_items` | M3 | Created with the orchestrator that writes them. |
| §9 table `approvals` | M5 | Created with the approval workflow, four-eyes rule (ADR-007) and the separate review role (ADR-013). `drafts` and `action_proposals` were created in M2 (ADR-013). |
| §9 table `asset_registry` | M8 | Created with operations and handover. |

M7's acceptance tests will include the §9 minimums: ≥30 passages, including one expired and one contradictory version, and ≥20 vendors across all risk tiers. The owner approved this placement on 2026-11-26.

**Known limitations (M1):**
- Rate limiting is in-process (per worker). `X-Forwarded-For` is not trusted, and users behind the same NAT share a client bucket (ADR-012). Edge rate limiting arrives with Cloudflare in M6.
- Unknown query parameters are ignored, not rejected. M2 tool schemas use `extra="forbid"`.
- Token revocation lists do not exist. Deactivating a user or removing a tenant takes effect on the next request, because the directory is re-read every time. A stolen token stays valid for its scopes until expiry (15 minutes).
- RLS is defence in depth, not authentication. Anyone holding a service-role password could set their own tenant scope (ADR-003).
- The secret check in CI is a small pattern scanner, not gitleaks.
- GitHub Actions are pinned to major tags, not commit SHAs.
- The CI workflow has no verified run on GitHub. No push has been verified.

**Build:** `pyproject.toml`, uv lock, docker-compose (Postgres 16), Alembic migrations for the schemas in
ADR-003, a deterministic seed generator (`scripts/seed.py --seed 42`), connector interfaces + mock
adapters, `domain/money.py`, `domain/contributions.py`, effective-dated rule lookup, mock JWT auth, a
minimal FastAPI read API, an audit table with hash chain, log redaction, and import-linter contracts.

**Also in M1 (owner decision):** Postgres roles per connector, row-level security on every
tenant-owned mock table, column-level grants and a trigger protecting `tickets.status` (ADR-003), and a
GitHub Actions workflow running lint, types, import contracts and all tests against a Postgres service
container.

**Seed:** 3 tenants, 5 plans (with an overlapping-rule conflict case), 50 participants (`PART-0001`…),
8 pay periods, about 400 payroll rows (including deliberate missing rows, duplicate rows and 1-cent and
larger mismatches), and 120 tickets across all 8 intents (including duplicate, injection, cross-tenant,
ambiguous-participant, unsupported request and pasted-SSN cases). The playbook and vendor data move to
M7, where they are used.

**Acceptance tests:**
- `test_seed_is_reproducible` (same seed gives an identical content hash)
- `test_money_rejects_float`, `test_round_half_up_table`, property test that `expected` is quantised to cents
- `test_rule_selection_half_open_interval`, `test_rule_conflict_detected`, `test_rule_missing_detected`
- `test_contribution_finding_match / discrepancy / missing_row / not_enrolled`, `test_one_cent_variance_is_discrepancy`
- `test_cross_tenant_read_denied` for each read endpoint (404 for objects, 403 for missing scope; ADR-003)
- `test_rls_*`: each service role sees zero rows without a tenant setting, only its tenant's rows with one, and cannot insert rows for another tenant
- `test_role_privileges_*`: each role is denied every schema and table outside its grant
- `test_writer_cannot_update_ticket_status` (column grant + trigger)
- `test_connector_has_no_status_mutation`, `test_api_has_no_write_routes`
- `test_audit_update_blocked`, `test_chain_verifier_detects_edit`
- `test_logs_do_not_contain_canary_token`
- `test_upload_with_ssn_column_rejected_and_not_stored`
- `lint-imports` passes, along with ruff and mypy (strict on `domain/`)

### M2: Service Operations MCP server (Python) ✅ (completed, reviewed, approved and committed 2026-11-26 as `91f959b`)

**Delivered:** the ten spec §6 tools on MCP Python SDK 2.3 (low-level `Server`, ADR-013), over stdio
and stateless Streamable HTTP. Includes Pydantic `extra="forbid"` inputs, scope-filtered
`tools/list`, per-call scope checks, per-call logging and metrics, audited denials,
`.mcp.json.example`, `scripts/dev_token.py`, a run guide in [mcp-service-ops.md](mcp-service-ops.md)
and an [Inspector transcript](mcp-inspector-transcript.md).

**Changes to earlier plans (documented in ADR-013):**
- `app.drafts`, `app.draft_versions` and `app.action_proposals` were created in **M2, not M5**, because
  M2's acceptance requires the create tools to write rows. Approvals stay in M5.
- New mock system `servicekb_mock` (fictional service policies) for `get_policy_snippets`, with its
  own role. The seed generator is now `seed-gen/2`, and the change is additive: tickets, cases and
  payroll are unchanged.
- Drafts are always `NEEDS_MANUAL_REVIEW` until the M3 validator exists. Evidence refs are verified
  server-side already: existence, ticket tenant, content hash, policy in force, and the caller's
  read scope for each cited record type. A reply must cite its own ticket.

**Independent review fixes (2026-11-26, ADR-013):**
- Migration 0003 adds database insert guards (initial states only; version 1 only, matching its
  header), TRUNCATE triggers and a per-tenant draft idempotency key.
- Evidence verification is scope-checked. Metrics keys are bounded. The SDK is pinned to `mcp>=2.3,<3`.
- `[::1]` was added to the Host allowlist, and standard-library logs are now redacted.
- Requester subjects carry the untrusted label, and bare 9-digit IDs are masked or refused.
- There is a safe upgrade path (`scripts/db_upgrade.py`), a 0002 downgrade that revokes its grants,
  a migration round-trip test, and [operations-migrations.md](operations-migrations.md).

**Final review fixes (2026-11-26, ADR-013, migration 0004):**
- Per-user idempotency keys; replay only for the same user; the evidence scope check runs before
  any replay; `skill_version` is part of the replay comparison.
- `created_by_kind` on draft headers, matched by version 1; M2 drafts are `NEEDS_MANUAL_REVIEW` only.
- Pinned `search_path` on all guard functions (including M1's); `hide_parameters=True` on all
  engines; runbook wording corrected.

**Final hardening (2026-11-26):** migration 0005 pins `sec.tenant_scope()`'s `search_path`
(behaviour, owner and grants unchanged; exact downgrade), with shadowing and RLS regression tests.

**Carried into later milestones (from ADR-013):**
- M3: server-side check of proposed queues against the rules engine; validator before insert;
  minimum-scope run tokens; re-allow `PENDING_REVIEW` in `app.guard_draft_insert` in the same
  migration as the validator.
- M5: a separate database role for edits, status changes, approvals and execution; a
  status-transition trigger; four-eyes over the authorship columns.

**Build:** the `mcp_servers/service_ops` tools 1–10 from spec §6, Pydantic I/O, token → principal
resolution, per-tool scope map, audit and metrics for each call, stdio + Streamable HTTP, and
`.mcp.json.example`.

**Acceptance:** MCP Inspector lists and calls the tools (screenshot or transcript in `docs/`).
`test_schema_rejects_extra_fields`, `test_page_size_capped`, `test_missing_scope_403`,
`test_cross_tenant_*` through MCP, and `test_registry_has_no_forbidden_tools`. `create_reply_draft`
and `propose_ticket_assignment` create rows only.

### M3: First skill and first worker (NOT STARTED; starts only with the owner's approval)
**Build:** `skills/service-ticket-triage/` (all content required by spec §7) and
`skills/contribution-investigation/`. Also the orchestrator state machine, `ModelProvider`,
`OllamaProvider`, `ScriptedProvider`, run tokens, budgets, evidence bundle, draft validator and routing
rules engine. Plus `.claude/skills/add-connector`, `add-mcp-tool`, `write-eval` and `security-review`.

**Acceptance:** one seeded discrepancy ticket runs end to end to `PENDING_REVIEW` with a correct
finding. Scripted adversarial runs (invented amount, cross-tenant call, unlisted tool, malformed JSON,
timeout) each end in `NEEDS_MANUAL_REVIEW`. No connector write happens during any run.

### M4: Evaluation suite
**Build:** 30 golden cases split into dev and held-out, covering normal, ambiguous, missing-data,
conflicting, cross-tenant and injection cases. Also scorers for the spec §12 metrics, a report with full
version metadata, and a CI workflow running the safety tier.

**Acceptance:** CI fails when a safety invariant is broken. This is shown by a deliberately broken
branch or test. A local Ollama report is committed.

### M5: Human-review UI and approved execution
**Build:** Streamlit operator queue, evidence and trace view, draft editor, approve/reject/escalate,
execution service, mock Zendesk post and assign.

**Acceptance:** `test_approve_wrong_hash_409`, `test_approve_stale_ticket_409`,
`test_agent_token_cannot_approve`, `test_double_approve_single_post`,
`test_crafted_close_request_rejected`, `test_human_edit_with_wrong_amount_blocked`.

### M6: Cloudflare remote MCP + OAuth
**Build:** a TS Worker (Agents SDK, `createMcpHandler`, Streamable HTTP), Workers OAuth Provider,
per-tool scope checks, forwarding to the API, a JSON-Schema contract test, and a Queue for ingest.

**Acceptance:** unauthenticated calls get `401`. Insufficient scope gets `403`. A scoped client calls
only permitted tools. A forged forwarded principal is rejected. Log capture contains no secrets. The
contract test is green.

### M7: Legal & Compliance worker
**Carries the §9 seed requirements moved from M1 (approved 2026-11-26):** ≥30 approved-answer passages with version, section, effective date and citation metadata (including one expired and one contradictory version), and ≥20 vendors across all risk tiers, with findings.

**Build:** a 30–50 passage synthetic playbook with versions (including one expired and one
contradictory), the compliance MCP tools, a vendor register with 25 vendors and findings, deterministic
vendor checks, the `due-diligence-response` and `vendor-oversight` skills, and a reviewer queue.

**Acceptance:** `test_every_claim_cited`, `test_expired_version_never_cited`,
`test_contradiction_raises_exception`, `test_vendor_draft_cannot_set_risk_tier`.

### M8: Operations, monitoring, handover
**Build:** asset registry (refuses operational-ready without backup and monitoring owners), monitoring
page (spec §14 metrics), kill switch, runbook, rollback, a backup/restore drill, handover doc,
release checklist and interview guide.

**Acceptance:** a clean-checkout quickstart works for a second developer following only the docs. All
tests and safety gates pass. Owners are recorded.

## Definition of done (every milestone)
- `ruff`, `mypy`, `lint-imports` and `pytest` run, with output pasted into the milestone summary.
- No secrets in Git (a `gitleaks` or equivalent pre-commit hook from M1).
- Docs are updated wherever behaviour changed.
