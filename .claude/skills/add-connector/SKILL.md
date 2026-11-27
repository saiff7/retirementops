---
name: add-connector
description: Add or extend a RetirementOps mock-system connector (a new schema, its own Postgres login role, RLS, a typed adapter and tests). Use when a new source system or a new read/write capability on an existing one is needed. Developer workflow for Claude Code, not a runtime agent skill.
---

# Add a connector (RetirementOps)

A connector is the only code that touches one mock system's schema, using its own database login
(ADR-003). Read `CLAUDE.md` first; its database rules apply in full. This skill is the checklist on
top of them.

## Before you start

- Restate which system owns the data (`docs/source-of-truth.md`) and why a connector is needed.
  If it needs a new role or a new schema, that is an architecture change: explain the trade-off
  and get the owner's approval (CLAUDE.md rule 10), then record it in `DECISIONS.md`.
- Decide read vs write. **Never** add a method that closes or solves tickets, changes contribution
  or enrollment records, moves money, or runs arbitrary SQL. `FORBIDDEN_METHOD_PATTERN` in
  `connectors/interfaces.py` is checked against every interface and adapter by the tests.

## Steps

1. **Migration** (new file, never edit an applied one): schema, tables with `tenant_id`, RLS enabled,
   policy `tenant_isolation` using `sec.tenant_scope()` for both `USING` and `WITH CHECK`, and the
   narrowest grants for one role only. Guard functions `SET search_path = pg_catalog, pg_temp`.
   `downgrade()` revokes everything `upgrade()` granted outside the dropped objects.
2. **Role:** add it to `DbRole` in `config.py`, a `ro_pw_*` setting, and `RO_PW_*=__generate__` to
   `.env.example`. `scripts/dev_bootstrap.py` appends the local secret; never hardcode one. One
   runtime role never gets two mock schemas, and `app_service` is never widened.
3. **Interface:** a `Protocol` in `connectors/interfaces.py` returning frozen domain records, each
   with a `SourceRef` from `make_source()` (system, type, id, version, retrieval time, hash).
4. **Adapter** `connectors/<system>_mock.py`: every query filters by tenant explicitly **and** runs
   in `scoped_tx(engine, scope)` so RLS applies. Money is integer cents in the database and
   `Money`/`Decimal` in records (ADR-006); never floats. Add the module to the independence
   contract in `pyproject.toml` (`[tool.importlinter]`).
5. **Wire** it only in `services/container.py`, and expose it through a service that calls
   `ctx.require(...)` scopes and, for agent runs, the `ctx.bound_*` run-binding checks. API, MCP
   and agent code never import connectors directly (import-linter enforces it).
6. **Evidence:** if its records can be cited in drafts, add them to `SUPPORTED`/`REQUIRED_SCOPE`
   in `services/evidence.py` with the owner fields the validator needs.
7. **Data classification:** add each new field to `docs/data-classification.md` (tool output,
   prompt, logs). Restricted data never reaches logs or prompts raw.

## Tests you must add or update

- `tests/security/test_db_roles_and_rls.py`: add the role to `READABLE`/`PROBE_TABLE` and prove it
  cannot read other schemas or perform destructive writes.
- RLS: no scope sees nothing; one tenant sees only itself; inserts for another tenant fail.
  Add the new schema to the schema list in `test_every_tenant_owned_table_has_rls_and_policy`
  (`tests/integration/test_schema_and_seed.py`): it only scans the schemas listed there.
- Migration round trip in `tests/integration/test_migrations.py` (head expectation, downgrade
  revokes grants).
- Connector unit/integration tests for cross-tenant reads returning `None`/404.

## Verify

```bash
uv run python scripts/dev_bootstrap.py && uv run ruff check src tests scripts migrations && uv run ruff format --check src tests scripts migrations && uv run mypy && uv run lint-imports && uv run pytest -q
```

Never run migrations against the dev database without the owner's approval (tests use their own
database). Update the five root documents per CLAUDE.md.
