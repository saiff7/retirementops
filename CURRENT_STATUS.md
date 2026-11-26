# Current status

_Last updated: 2026-11-27 (end of the M2 hardening session)._

## Milestones

| Milestone | State | Commit |
|---|---|---|
| M0 Discovery and design | Done, approved | `ac9e36b` |
| M1 Synthetic business backend | Done, approved | `ac9e36b` |
| **M2 Service Operations MCP server** | **Implemented and reviewed three times; awaiting owner approval; not committed** | — |
| M3 First skill and worker | Not started (do not start before M2 approval) | — |
| M4–M8 | Not started | — |

## M2 implementation status

- **Delivered:**
  - the ten spec §6 tools over stdio and Streamable HTTP;
  - scope-filtered discovery, Pydantic `extra="forbid"` inputs, audited denials, metrics;
  - drafts and proposals as rows only, with server-side evidence verification;
  - the [Inspector transcript](docs/mcp-inspector-transcript.md).
- **Review fixes:** H-1, M-1 to M-3, Low-1 to Low-9, N-1 and its lows, and the final
  `sec.tenant_scope()` pin. All are listed in [ADR-013](docs/adr/013-mcp-server-implementation.md).
- **Database:** migrations 0001–0005. The dev database (`retirementops`) is upgraded in place to
  **0005** with its data kept. The test database is rebuilt on every test run.

## Latest verified results (2026-11-27, this working tree)

| Check | Result |
|---|---|
| Full test suite | **449 passed, 0 failed** (unit 162, integration 105, security 182) |
| Coverage | **97%** (3,006 statements, 96 missed) |
| Migration round trip, safe upgrade, shared-key downgrade | 3 passed |
| `sec.tenant_scope()` hardening (pin, unchanged behaviour, shadowing, roles) | 33 passed |
| Runtime roles and RLS | 84 passed |
| Idempotency and review regressions | 29 passed |
| M1 API, abuse protection and audit privacy | 47 passed |
| MCP tools, transports and registry | 107 passed |
| Ruff lint and format | Clean (99 files formatted) |
| mypy | Clean (61 source files) |
| Import contracts | 5 kept, 0 broken |
| Lockfile | Consistent |
| Secret scan | Passed (141 tracked files); no local secret values or JWTs in changed files |
| Live adversarial re-runs | N-1 replay closed; 13 database guard bypass attempts as `app_service` all blocked; RLS unaffected by the caller's `search_path`; real database-failure logging (SDK and HTTP) leaks nothing |

CI has **not** run on GitHub (nothing pushed since the M1 commit).

## Remaining work

1. Owner review and approval of M2, then commit (see [HANDOFF.md](HANDOFF.md)).
2. **M3** ([plan](docs/implementation-plan.md)):
   - `service-ticket-triage` and `contribution-investigation` skills;
   - the orchestrator, `ModelProvider` (Ollama, Scripted), and run-scoped agent tokens;
   - the server-side draft validator, which re-allows `PENDING_REVIEW`;
   - the routing rules engine and a server-side check of proposed queues;
   - developer skills under `.claude/skills/`.

Open risks and debt: [KNOWN_ISSUES.md](KNOWN_ISSUES.md).
