# Known issues

_Last updated: 2026-11-26._ Three sections, kept separate on purpose:
- **Open:** unresolved; should be fixed.
- **Deferred:** planned for a named milestone.
- **Accepted:** a deliberate, documented trade-off for this prototype.

There are no known critical, high or medium open security findings.

## Open (unresolved)

| ID | Severity | Issue | Recommended resolution |
|---|---|---|---|
| OPEN-1 | Medium (process) | CI has never run on GitHub; the workflow is unverified there. | Push a branch and confirm `.github/workflows/ci.yml` passes before relying on it. |
| OPEN-2 | Low | GitHub Actions are pinned to major tags, not commit SHAs. | Pin `actions/checkout` and `astral-sh/setup-uv` to SHAs. |
| OPEN-3 | Low | The REST API ignores unknown query parameters (MCP tools reject extras). | Add a strict query-parameter dependency to the API routes. |
| OPEN-4 | Low | All tests share one session database; ordering issues have occurred once. | Per-test transactions or per-module databases for mutating tests. |
| OPEN-5 | Low | Coverage does not measure code run in subprocesses or threads (`__main__.py` at 83%, `http.py` at 88%); ticket search filters and the writer's lost-race branch are untested. | Enable coverage for subprocesses; add the two targeted tests. |
| OPEN-6 | Low | The CI secret check is a small pattern scanner, not gitleaks. | Add gitleaks (or equivalent) to CI and pre-commit. |
| OPEN-7 | Low | Starlette's TestClient warns that `httpx` should become `httpx2`; MCP Inspector 2.14.0 wants Node ≥ 24.22.1 (24.8.0 installed; it works). | Switch the test client dependency; upgrade Node. |
| OPEN-8 | Low (perf) | `sec.tenant_scope()` can no longer be inlined, because its `SET search_path` prevents it, so RLS calls it per check. | Negligible now; measure with `EXPLAIN ANALYZE` if row counts grow. |

## Deferred to a milestone

| Milestone | Item |
|---|---|
| M3 | The agent principal has no tenant access until run-scoped tokens exist. Proposed queues aren't checked against the routing rules engine. The draft validator, and re-allowing `PENDING_REVIEW`, come with it. |
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
| ACC-11 | Passing the token with `-e` to the Inspector exposes it on the local command line | Only for 15-minute local dev tokens on synthetic data. |
