# Known issues

_Last updated: 2026-11-26._ Three sections, kept separate on purpose:
- **Open:** unresolved; should be fixed.
- **Deferred:** planned for a named milestone.
- **Accepted:** a deliberate, documented trade-off for this prototype.

There are no known critical, high or medium open security findings.

## Open (unresolved)

| ID | Severity | Issue | Recommended resolution |
|---|---|---|---|
| OPEN-1 | Medium (process) | CI has no verified run on GitHub. No push has been verified, and no CI result has been observed. | Push a branch and confirm `.github/workflows/ci.yml` passes before relying on it. |
| OPEN-2 | Low | GitHub Actions are pinned to major tags, not commit SHAs. | Pin `actions/checkout` and `astral-sh/setup-uv` to SHAs. |
| OPEN-3 | Low | The REST API ignores unknown query parameters (MCP tools reject extras). | Add a strict query-parameter dependency to the API routes. |
| OPEN-4 | Low | All tests share one session database; ordering issues have occurred once. | Per-test transactions or per-module databases for mutating tests. |
| OPEN-5 | Low | Coverage does not measure code run in subprocesses or threads (`__main__.py` at 83%, `http.py` at 88%); ticket search filters and the writer's lost-race branch are untested. | Enable coverage for subprocesses; add the two targeted tests. |
| OPEN-6 | Low | The CI secret check is a small pattern scanner, not gitleaks. | Add gitleaks (or equivalent) to CI and pre-commit. |
| OPEN-7 | Low | Starlette's TestClient warns that `httpx` should become `httpx2`; MCP Inspector 2.14.0 wants Node ≥ 24.22.1 (24.8.0 installed; it works). | Switch the test client dependency; upgrade Node. |
| OPEN-9 | Low | Stage 6 review minors, not fixed: a failed run still proposes the routed queue without the failure in its rationale; another tenant's plan NAME in a ticket is ignored rather than flagged; the worker cannot be called from inside an event loop (it uses `anyio.run`; M5 must use a thread); wall-clock budget equals the 10-minute token lifetime and is checked between calls only; the kill switch is read at run start only; the orchestrator holds the whole service container; the trace's evidence list omits the ticket record; all runs share one per-principal rate bucket. All fail closed or are harmless today. | Address with M5 (UI/service wrapper) and M8 (operations), or earlier if the owner prefers. |
| OPEN-10 | Low | Second stage 6 review minors: the host's draft-writing token could also create a proposal (scopes are per access type, not per action; same binding and permitted queue, 120 s, host-held only); if the final audit write fails, `run()` raises after the draft is stored (the trace is written first); a crafted 10,000-character body takes about 0.2 s to validate (bounded); write-token expiry is checked in the claims, not by a test against the server. | Bind write tokens to one action (M5); catch audit failures in the run wrapper (M5). |
| OPEN-8 | Low (perf) | `sec.tenant_scope()` can no longer be inlined, because its `SET search_path` prevents it, so RLS calls it per check. | Negligible now; measure with `EXPLAIN ANALYZE` if row counts grow. |

## Deferred to a milestone

| Milestone | Item |
|---|---|
| M3 | Done (committed in `1515ed2`): run-scoped tokens, the server-side queue check, the draft validator and `PENDING_REVIEW` (migration 0006). Still to come in M3: skills, providers, the orchestrator and run-trace storage. Prompt construction now masks employer employee refs (`E-…`) and SSN-like values (`agent_runtime/providers/prompts.py`); `get_ticket_details` itself still masks only SSN-like values, so other MCP clients see `E-…` refs. The spec's `skills/service-ticket-triage/eval-cases.jsonl` is left to M4 (golden cases). |
| M5 | A separate database role for edits, status changes, approvals and execution; a status-transition trigger; four-eyes enforcement; UI upload route for payroll files. |
| M6 | OAuth 2.1 at the Cloudflare edge; remote (non-loopback) MCP exposure; edge rate limiting. |
| M7 | Compliance MCP server; playbook (≥30 passages) and vendor (≥20) seed data. |
| M8 | Exported metrics, dashboards and alerts; backup and restore automation; retention; audit-chain CLI; operational-readiness registry. |

## Accepted risks (documented trade-offs)

| ID | Risk | Why accepted / mitigation |
|---|---|---|
| ACC-1 | Mock identity, not SSO; no token revocation list (tokens valid up to 15 min) | Demo scope. Deactivation and tenant removal take effect immediately because the directory is re-read per request. |
| ACC-2 | RLS limits what roles see but isn't authentication: a holder of a service-role password could set its own tenant scope | Role passwords are secrets held only by the app (ADR-003). |
| ACC-3 | `app_service` stamps the creator from the verified token; a fully compromised process could create a new version-1 draft under another user's name | It still cannot approve, append versions or execute; the M5 role split keeps that true (ADR-013). |
| ACC-4 | Guards are tamper-resistant, not immutable: the table owner can disable triggers | Same as the audit log (ADR-009); the owner role is used only by migrations and the seed. |
| ACC-5 | Audit log is tamper-evident, not immutable | Chain verifier detects edits; external anchoring is future work. |
| ACC-6 | Rate limits are per process; `X-Forwarded-For` isn't trusted; users behind the same NAT share a bucket | Loopback-only until M6, where edge limits are added (ADR-012). |
| ACC-7 | No database foreign key ties a draft's ticket to its tenant (separate systems) | The service reads the ticket under RLS before writing (ADR-013). |
| ACC-8 | A database outage during authentication returns 500, not 503 | Fails closed with a generic body and redacted logs. |
| ACC-9 | Rollback of migration 0002 deletes all drafts and proposals | Documented, with a backup step ([operations-migrations.md](docs/operations-migrations.md)). |
| ACC-10 | Audit pseudonyms are keyed: anyone with `RO_AUDIT_PEPPER` can confirm a known ref | Intended investigation path. |
| ACC-12 | A run's trace is written once, when the run ends; a process crash mid-run loses that run's trace | Append-once keeps traces immutable without UPDATE rights. Drafts and proposals created by the run are still audited with its `run_id`. The same-transaction guard also assumes no SAVEPOINT around the run insert (none is used). |
| ACC-13 | If writing a run's trace fails, the run's outcome (and any draft) stands without a trace | Never silent: the run reason `TRACE_NOT_WRITTEN`, `trace_written: false` in `agent_run.finished`, and a dedicated `agent_run.trace_failed` audit event. Failing the run would not undo the audited draft. |
| ACC-14 | An `Orchestrator` instance handles one run at a time | Enforced with a non-blocking lock held for the whole run, including proposal, trace and audit; a second run on the same instance (any thread) raises `RuntimeError`. Create one instance per concurrent run. |
| ACC-11 | Passing the token with `-e` to the Inspector exposes it on the local command line | Only for 15-minute local dev tokens on synthetic data. |
