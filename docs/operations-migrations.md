# Database migrations: upgrade and rollback

> Local development and CI only (synthetic data). A real deployment needs a reviewed change process,
> backups and a tested restore (M8).

## Upgrade an existing database (keeps data)

Migrations run as `ro_owner`, which cannot create login roles. New migrations may grant privileges to
new connector roles (0002 grants to `svc_servicekb_reader`). Always create roles before migrating.

```bash
uv run python scripts/dev_bootstrap.py
```

That appends any new role passwords to `.env` and never overwrites existing values. Then:

```bash
uv run python scripts/db_upgrade.py
```

That ensures the roles, ensures the database (never dropping it) and runs all migrations. It is
idempotent; `tests/integration/test_migrations.py` runs it twice and checks that data survives.

Running `alembic upgrade head` alone works only if the roles already exist. If a role is missing, the
migration fails with `role ... does not exist` and its transaction rolls back cleanly
(`transaction_per_migration=True`).

## Rebuild from scratch (destroys data)

```bash
uv run python scripts/seed.py --seed 42 --reset
```

## Rollback

| Downgrade | Effect |
|---|---|
| `0005 → 0004` | Restores the exact 0001 definition of `sec.tenant_scope()` (unpinned `search_path`). Behaviour is identical; only the shadowing hardening is lost. No data change. |
| `0004 → 0003` | Restores per-tenant idempotency keys and the 0003 guard definitions (no `created_by_kind`; unpinned search paths). If two users in one tenant used the same key, only the oldest draft keeps it; **no draft is deleted**. Weakens N-1 protections. |
| `0003 → 0002` | Removes the insert guards, the TRUNCATE triggers and the draft idempotency key. **Weakens H-1 protections**, so only do it as part of a full rollback. |
| `0002 → 0001` | **Drops every draft, draft version and proposal** and the policy KB, and revokes the KB role's grants on `sec`. There is no in-database recovery: take a dump first. |
| `0001 → base` | Drops all schemas. |

```bash
docker compose exec -T db pg_dump -U postgres -d retirementops -Fc > /tmp/retirementops-before-rollback.dump
```

```bash
uv run alembic downgrade 0002
```

Login roles are cluster-wide and are not removed by downgrades. After a downgrade they keep
`CONNECT` on the database (granted by the bootstrap) but no schema, table or function privileges
for the objects that were dropped. To remove access entirely, revoke `CONNECT` or drop the database.

The full round trip (head → 0003 → head → 0002 → 0001 → head → base) runs in CI as
`test_migration_round_trip`, and the shared-key downgrade as
`test_downgrade_0004_keeps_drafts_when_users_shared_a_key`. The 0005 ↔ 0004 step is covered by
`tests/security/test_tenant_scope_hardening.py`.
