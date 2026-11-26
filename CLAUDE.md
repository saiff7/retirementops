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

## Layout
See `docs/architecture.md` §6 and ADR-002. Code lives in `src/retirementops/`:
`domain/` (pure; mypy strict), `connectors/` (one module per mock system and DB role),
`governance/` (audit, identity, tokens, logging), `services/` (enforcement point + composition root),
`api/` (FastAPI), `db/` (engines, bootstrap, migrations runner), `seed/` (deterministic generator).
Layer rules are machine-checked by import-linter (`[tool.importlinter]` in `pyproject.toml`).

## Commands
- Setup: `uv sync && uv run python scripts/dev_bootstrap.py && docker compose up -d --wait db`
- Seed dev DB: `uv run python scripts/seed.py --seed 42 --reset`
- All checks: `uv run ruff check src tests scripts migrations && uv run ruff format --check src tests scripts migrations && uv run mypy && uv run lint-imports && uv run pytest -q`
- Run the API: `uv run uvicorn retirementops.api.main:create --factory --host 127.0.0.1 --port 8000`
- After changing the generator: `uv run python scripts/seed.py --write-manifest` (commits `data/synthetic/*`)

## Database rules
- Any new tenant-owned table needs `tenant_id`, RLS enabled and the `tenant_isolation` policy.
  `test_every_tenant_owned_table_has_rls_and_policy` discovers tables automatically.
- Grants go in migrations, per role, as narrow as possible. Never grant one runtime role access to two mock schemas.
- Runtime code never uses `ro_owner`. Only migrations, the seed loader and tests do.
- Audit writes go through `services.audit_service.AuditService` (pseudonymises). `AuditLog` refuses raw `PART-`/`E-`/SSN-like values, so never work around that by writing to the table directly.
