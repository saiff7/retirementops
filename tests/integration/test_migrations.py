"""Migration safety (review Low-3): full round trip, clean rollback of grants, safe in-place upgrade.

Runs on a throwaway database that is dropped afterwards. Login roles are cluster-wide and shared.
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest
from sqlalchemy import Engine, create_engine, text

from retirementops.config import DbRole, Settings
from retirementops.db.bootstrap import create_database
from retirementops.db.migrate import bootstrap_and_upgrade, downgrade_to, upgrade_head

pytestmark = pytest.mark.integration
DB = "retirementops_migration_test"


@pytest.fixture
def scratch_db(settings: Settings, test_db: str) -> Iterator[Engine]:
    create_database(settings, DB, drop_existing=True)
    engine = create_engine(settings.dsn(DbRole.OWNER, DB))
    yield engine
    engine.dispose()
    with psycopg.connect(**settings.superuser_conninfo(), autocommit=True) as conn:  # type: ignore[arg-type]
        conn.execute(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)')


def _state(engine: Engine) -> dict[str, object]:
    with engine.connect() as c:
        version = None
        if c.execute(text("SELECT to_regclass('public.alembic_version')")).scalar():
            version = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
        schemas = sorted(
            r[0]
            for r in c.execute(
                text(
                    "SELECT nspname FROM pg_namespace WHERE nspname IN "
                    "('app', 'sec', 'zendesk_mock', 'planadmin_mock', 'payroll_mock', 'servicekb_mock')"
                )
            )
        )
        guards = c.execute(
            text(
                "SELECT count(*) FROM pg_trigger WHERE tgname IN ('drafts_guard_insert', "
                "'draft_versions_guard_insert', 'action_proposals_guard_insert', "
                "'drafts_no_truncate', 'draft_versions_no_truncate', 'action_proposals_no_truncate')"
            )
        ).scalar_one()
    return {"version": version, "schemas": schemas, "guards": guards}


def _pinned(engine: Engine) -> dict[str, bool]:
    with engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT p.proname, coalesce(array_to_string(p.proconfig, ','), '') LIKE "
                "'%search_path=pg_catalog, pg_temp%' FROM pg_proc p JOIN pg_namespace n ON "
                "n.oid = p.pronamespace WHERE n.nspname IN ('app', 'zendesk_mock') AND p.prokind = 'f'"
            )
        )
        return {r[0]: bool(r[1]) for r in rows}


def _index_columns(engine: Engine, index: str) -> str | None:
    with engine.connect() as c:
        return c.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = :i"), {"i": index}
        ).scalar()


def test_migration_round_trip(scratch_db: Engine) -> None:
    upgrade_head(DB)
    pinned = _pinned(scratch_db)
    assert pinned and all(pinned.values()), pinned  # final review Low-3, includes the M1 guard
    assert "guard_ticket_status" in pinned
    assert "created_by, idempotency_key" in (
        _index_columns(scratch_db, "drafts_idempotency_per_user") or ""
    )
    downgrade_to("0003", DB)
    assert not any(_pinned(scratch_db).values())  # exact 0003 definitions restored
    assert _index_columns(scratch_db, "drafts_idempotency_per_user") is None
    assert _index_columns(scratch_db, "drafts_idempotency") is not None
    upgrade_head(DB)
    assert _state(scratch_db) == {
        "version": "0005",
        "guards": 6,
        "schemas": [
            "app",
            "payroll_mock",
            "planadmin_mock",
            "sec",
            "servicekb_mock",
            "zendesk_mock",
        ],
    }
    downgrade_to("0002", DB)
    assert _state(scratch_db)["guards"] == 0
    downgrade_to("0001", DB)
    after_0001 = _state(scratch_db)
    assert after_0001["version"] == "0001" and "servicekb_mock" not in after_0001["schemas"]
    with scratch_db.connect() as c:  # 0002's downgrade revokes what it granted outside its schema
        assert (
            c.execute(
                text("SELECT has_schema_privilege('svc_servicekb_reader', 'sec', 'USAGE')")
            ).scalar()
            is False
        )
        assert (
            c.execute(
                text(
                    "SELECT has_function_privilege('svc_servicekb_reader', 'sec.tenant_scope()', 'EXECUTE')"
                )
            ).scalar()
            is False
        )
    upgrade_head(DB)
    assert _state(scratch_db)["version"] == "0005"
    downgrade_to("base", DB)
    assert _state(scratch_db) == {"version": None, "schemas": [], "guards": 0}


def test_bootstrap_and_upgrade_is_idempotent_and_keeps_data(
    scratch_db: Engine, settings: Settings
) -> None:
    bootstrap_and_upgrade(settings, DB)
    with scratch_db.begin() as c:
        c.execute(
            text(
                "INSERT INTO planadmin_mock.tenants VALUES ('TEN-KEEP', 'Kept (synthetic)', "
                "1, now())"
            )
        )
    bootstrap_and_upgrade(settings, DB)  # second run: no drop, no error
    with scratch_db.connect() as c:
        assert c.execute(text("SELECT count(*) FROM planadmin_mock.tenants")).scalar_one() == 1
    assert _state(scratch_db)["version"] == "0005"


def test_downgrade_0004_keeps_drafts_when_users_shared_a_key(scratch_db: Engine) -> None:
    """Per-user keys can collide per tenant; downgrading must not fail or delete drafts."""
    upgrade_head(DB)
    with scratch_db.begin() as c:  # owner seeds the minimum rows directly
        c.execute(text("INSERT INTO planadmin_mock.tenants VALUES ('TEN-ALDER', 'x', 1, now())"))
        for i, user in enumerate(("op.alex", "rev.sam")):
            c.execute(
                text(
                    "INSERT INTO app.drafts (draft_id, ticket_id, tenant_id, status, status_reasons, "
                    "created_by, created_at, idempotency_key, created_by_kind) VALUES "
                    "(:d, 'ZD-1001', 'TEN-ALDER', 'NEEDS_MANUAL_REVIEW', '[]', :u, now() + :i * "
                    "interval '1 second', 'shared-key-1', 'human')"
                ),
                {"d": f"DRF-000000000000000{i}", "u": user, "i": i},
            )
    downgrade_to("0003", DB)
    with scratch_db.connect() as c:
        rows = c.execute(
            text("SELECT created_by, idempotency_key FROM app.drafts ORDER BY created_at")
        ).all()
    assert [tuple(r) for r in rows] == [("op.alex", "shared-key-1"), ("rev.sam", None)]
