from __future__ import annotations

import pytest
from sqlalchemy import Engine, MetaData, text

from retirementops.connectors import payroll_mock, planadmin_mock, servicekb_mock, zendesk_mock
from retirementops.governance import audit, identity
from retirementops.seed.generator import Dataset
from retirementops.store import workflow
from tests.conftest import LOADED_DB_HASH

pytestmark = pytest.mark.integration


def test_seed_loaded_matches_generator(dataset: Dataset) -> None:
    """The database held exactly the generated content right after loading (byte-for-byte)."""
    assert LOADED_DB_HASH["value"] == dataset.content_hash()


def test_every_tenant_owned_table_has_rls_and_policy(
    owner_engine: Engine, dataset: Dataset
) -> None:
    """Auto-discovers tables: any future table with a tenant_id column must also get RLS."""
    with owner_engine.connect() as conn:
        rows = (
            conn.execute(
                text("""
            SELECT c.table_schema || '.' || c.table_name AS t, cl.relrowsecurity AS rls,
                   (SELECT count(*) FROM pg_policies p
                     WHERE p.schemaname = c.table_schema AND p.tablename = c.table_name) AS policies
            FROM information_schema.columns c
            JOIN pg_class cl ON cl.relname = c.table_name
            JOIN pg_namespace n ON n.oid = cl.relnamespace AND n.nspname = c.table_schema
            WHERE c.column_name = 'tenant_id'
              AND c.table_schema IN ('zendesk_mock', 'planadmin_mock', 'payroll_mock')
        """)
            )
            .mappings()
            .all()
        )
    assert len(rows) == 11
    missing = [r["t"] for r in rows if not r["rls"] or r["policies"] < 1]
    assert missing == []


def test_runtime_roles_cannot_bypass_anything(owner_engine: Engine, dataset: Dataset) -> None:
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text("""
            SELECT rolname FROM pg_roles
            WHERE (rolname LIKE 'svc\\_%' OR rolname = 'app_service')
              AND (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication)
        """)
        ).all()
        members = conn.execute(
            text("""
            SELECT r.rolname FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member
            WHERE r.rolname LIKE 'svc\\_%' OR r.rolname = 'app_service'
        """)
        ).all()
    assert rows == [] and members == []


@pytest.mark.parametrize(
    "md",
    [
        zendesk_mock.metadata,
        planadmin_mock.metadata,
        payroll_mock.metadata,
        servicekb_mock.metadata,
        workflow.app_metadata,
        audit.app_metadata,
        identity.app_metadata,
    ],
    ids=lambda m: str(m.schema),
)
def test_core_tables_match_database(owner_engine: Engine, dataset: Dataset, md: MetaData) -> None:
    """Connector table definitions must match the migrated schema exactly (no drift)."""
    with owner_engine.connect() as conn:
        for t in md.tables.values():
            db_cols = {
                r[0]
                for r in conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = :s AND table_name = :t"
                    ),
                    {"s": t.schema, "t": t.name},
                )
            }
            assert db_cols == set(t.c.keys()), t.fullname


def test_app_tenant_tables_have_rls(owner_engine: Engine, dataset: Dataset) -> None:
    """RetirementOps' own tenant-owned tables (drafts, proposals) are RLS-protected too.
    audit_events is deliberately cross-tenant (system of record for all decisions)."""
    with owner_engine.connect() as conn:
        rows = (
            conn.execute(
                text("""
            SELECT c.table_name, cl.relrowsecurity AS rls
            FROM information_schema.columns c
            JOIN pg_class cl ON cl.relname = c.table_name
            JOIN pg_namespace n ON n.oid = cl.relnamespace AND n.nspname = c.table_schema
            WHERE c.column_name = 'tenant_id' AND c.table_schema = 'app'
        """)
            )
            .mappings()
            .all()
        )
    tables = {r["table_name"]: r["rls"] for r in rows}
    assert tables.pop("audit_events") is False
    assert tables == {"drafts": True, "draft_versions": True, "action_proposals": True}
