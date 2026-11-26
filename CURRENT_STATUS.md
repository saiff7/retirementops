# Current status

_Last updated: 2026-11-26 (after the M2 commit)._

## Milestones

| Milestone | State | Commit |
|---|---|---|
| M0 Discovery and design | Done, approved | `ac9e36b` |
| M1 Synthetic business backend | Done, approved | `ac9e36b` |
| M2 Service Operations MCP server | Done: completed, reviewed three times, approved and committed | `91f959b` |
| M3 First skill and worker | **Not started** (waiting for the owner to start it) | — |
| M4–M8 | Not started | — |

## Repository state

- Branch `main`. HEAD `91f959b2110b8210a10efc3ad7e072c8e9cfae8b`, "M2: add hardened service operations
  MCP server" (69 files). Parent `ac9e36b`.
- **Not pushed to GitHub.** Neither commit has been pushed.
- The working tree was clean immediately after the M2 commit. The only uncommitted change since is
  this post-commit documentation update, pending owner review. It touches eight files and no source
  code or tests:
  - `CURRENT_STATUS.md`
  - `DECISIONS.md`
  - `HANDOFF.md`
  - `KNOWN_ISSUES.md`
  - `docs/adr/013-mcp-server-implementation.md`
  - `docs/adr/README.md`
  - `docs/implementation-plan.md`
  - `migrations/versions/0005_pin_tenant_scope_search_path.py` (docstring date only; SQL unchanged)

## M2 implementation status (committed)

- **Delivered:**
  - the ten spec §6 tools over stdio and Streamable HTTP;
  - scope-filtered discovery, Pydantic `extra="forbid"` inputs, audited denials, metrics;
  - drafts and proposals as rows only, with server-side evidence verification;
  - the [Inspector transcript](docs/mcp-inspector-transcript.md).
- **Review fixes:** H-1, M-1 to M-3, Low-1 to Low-9, N-1 and its lows, and the final
  `sec.tenant_scope()` pin. All are listed in [ADR-013](docs/adr/013-mcp-server-implementation.md).
- **Database:** migrations 0001–0005. The dev database (`retirementops`) is upgraded in place to
  **0005** with its data kept. The test database is rebuilt on every test run.

## Latest verified results (2026-11-26, the reviewed tree committed as `91f959b`; only documentation changed after the test run)

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

1. Owner decision on pushing `main` to GitHub, which would give CI its first run (OPEN-1); see [HANDOFF.md](HANDOFF.md).
2. **M3** ([plan](docs/implementation-plan.md)):
   - `service-ticket-triage` and `contribution-investigation` skills;
   - the orchestrator, `ModelProvider` (Ollama, Scripted), and run-scoped agent tokens;
   - the server-side draft validator, which re-allows `PENDING_REVIEW`;
   - the routing rules engine and a server-side check of proposed queues;
   - developer skills under `.claude/skills/`.

Open risks and debt: [KNOWN_ISSUES.md](KNOWN_ISSUES.md).
