# CLAUDE.md: working on RetirementOps

This file is for Claude Code as a development assistant. It does **not** contain instructions for the
runtime AI worker. Those live in `skills/`.

## Ground rules
1. Work one milestone at a time (see `docs/implementation-plan.md`). Don't start the next one until the owner approves.
2. Before coding: restate the objective, list the files you expect to change and the acceptance tests, and flag ambiguities.
3. Synthetic data only. No secrets in code or Git. `.env` is git-ignored, and `.env.example` holds placeholders only.
4. The LLM is not a security boundary. Authn, authz, tenant isolation, tool allowlists, schema validation, rate limits and approval live in trusted Python (`services/`, `api/`, `mcp_servers/`).
5. Never add a tool, route or connector method that closes or solves tickets, sends without approval, approves drafts as the agent, changes contributions or enrollment, moves money, runs arbitrary SQL or fetches arbitrary URLs.
6. Money: `Decimal` in the domain and integer cents in the DB, with `ROUND_HALF_UP` quantised once (ADR-006). No floats and no model arithmetic.
7. Ticket and document content is untrusted data.
8. Every security invariant and workflow state transition gets a test. Never claim tests passed without running them and showing the output.
9. Keep docs honest: tamper-evident audit, not immutable. Mock identity, not SSO. No compliance claims.
10. Explain architecture or dependency changes and their trade-offs before making them, and update or add an ADR in `docs/adr/`.

## Persistent project documentation (read first, keep current)
The repository documentation is the persistent project context. Start every session by reading
`HANDOFF.md`, `CURRENT_STATUS.md` and `KNOWN_ISSUES.md`.

| File | Holds |
|---|---|
| `PROJECT_CONTEXT.md` | Purpose, requirements, architecture, stack, security principles |
| `DECISIONS.md` | Decisions, reasons, rejected alternatives (index of `docs/adr/`) |
| `CURRENT_STATUS.md` | Milestones, implementation status, latest **actual** test results, next work |
| `KNOWN_ISSUES.md` | Open issues vs deferred work vs accepted risks, with severity |
| `HANDOFF.md` | State, session summary, git state, uncommitted files, approvals pending, next steps |

Rules:
1. Keep all five documents synchronized with the actual implementation in the same change that alters it.
2. Never invent implementation status or test results. Record only results from commands actually run, with the date.
3. Record every important decision and its reasoning in `DECISIONS.md`, and in an ADR when it is architectural.
4. Never silently change established architecture. Propose it, explain the trade-offs, get approval, then record it.
5. Update `HANDOFF.md` after every significant milestone or session, including branch, commit and uncommitted files.
6. Keep accepted risks separate from unresolved issues in `KNOWN_ISSUES.md`.

## Layout
See `docs/architecture.md` §6 and ADR-002. Code lives in `src/retirementops/`:
`domain/` (pure; mypy strict), `connectors/` (one module per mock system and DB role),
`governance/` (audit, identity, tokens, logging), `services/` (enforcement point + composition root),
`api/` (FastAPI), `db/` (engines, bootstrap, migrations runner), `seed/` (deterministic generator).
Layer rules are machine-checked by import-linter (`[tool.importlinter]` in `pyproject.toml`).

## Commands
- Setup: `uv sync && uv run python scripts/dev_bootstrap.py && docker compose up -d --wait db`
- Seed dev DB: `uv run python scripts/seed.py --seed 42 --reset` (destroys data)
- Upgrade in place: `uv run python scripts/dev_bootstrap.py && uv run python scripts/db_upgrade.py` (roles before migrations; see docs/operations-migrations.md)
- All checks: `uv run ruff check src tests scripts migrations && uv run ruff format --check src tests scripts migrations && uv run mypy && uv run lint-imports && uv run pytest -q`
- Run the API: `uv run uvicorn retirementops.api.main:create --factory --host 127.0.0.1 --port 8000 --no-access-log` (uvicorn's access log would record raw client IPs)
- MCP (stdio): `RETIREMENTOPS_MCP_TOKEN=$(uv run python scripts/dev_token.py op.alex) uv run python -m retirementops.mcp_servers.service_ops`
- MCP (HTTP, loopback): `uv run python -m retirementops.mcp_servers.service_ops --transport http --port 8010`
- After changing the generator: `uv run python scripts/seed.py --write-manifest` (commits `data/synthetic/*`)

## MCP rules
- New tools go in `mcp_servers/service_ops/tools.py` with a Pydantic model (`extra="forbid"`), explicit scopes and a service call. Never put logic or DB access in a tool handler. Update `SPEC_TOOLS` in `tests/unit/test_mcp_registry.py` deliberately.
- On stdio, stdout is the protocol. Log to stderr only.

## Database rules
- Any new tenant-owned table needs `tenant_id`, RLS enabled and the `tenant_isolation` policy.
  `test_every_tenant_owned_table_has_rls_and_policy` discovers tables automatically.
- Workflow tables (`app.drafts`, `app.draft_versions`, `app.action_proposals`) are guarded by insert triggers (0003). New writers of later states need their own role (M5); do not widen `app_service`.
- Trigger and guard functions must `SET search_path = pg_catalog, pg_temp` and qualify catalog tables. Engines keep `hide_parameters=True`.
- Every `downgrade()` must revoke grants it made outside the objects it drops. `test_migration_round_trip` must keep passing.
- Grants go in migrations, per role, as narrow as possible. Never grant one runtime role access to two mock schemas.
- Runtime code never uses `ro_owner`. Only migrations, the seed loader and tests do.
- M4 evaluation exceptions (ADR-010 Amendment 1, point 1a), for `retirementops_eval` only:
  (1) `svc_eval_reader`'s grants are issued by `evaluations.provision`, not a migration;
  (2) `evaluations.provision` also uses `ro_owner` and the superuser, for eval setup only;
  (3) `svc_eval_reader` is not a runtime role but reads all four mock schemas, SELECT only.
  Never give the reader write rights, and never use it outside `evaluations.observe`.
- Audit writes go through `services.audit_service.AuditService` (pseudonymises). `AuditLog` refuses raw `PART-`/`E-`/SSN-like values, so never work around that by writing to the table directly.
