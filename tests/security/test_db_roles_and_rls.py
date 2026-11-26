"""Database-level least privilege (ADR-003): per-connector roles, grants, RLS, status protection.

These tests talk to Postgres directly as each runtime role. They prove isolation holds even if
application code forgot a WHERE clause.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
import sqlalchemy.exc
from sqlalchemy import Engine, text

from retirementops.config import DbRole
from retirementops.connectors.interfaces import NotVisibleError
from retirementops.connectors.zendesk_mock import ZendeskMockWriter
from retirementops.domain.authz import TenantScope
from retirementops.domain.taxonomy import Queue
from retirementops.seed.generator import Dataset

pytestmark = pytest.mark.integration

RoleEngine = Callable[[DbRole], Engine]

# Which schemas each runtime role may read at all.
READABLE = {
    DbRole.APP_SERVICE: {"app"},
    DbRole.ZENDESK_READER: {"zendesk_mock"},
    DbRole.ZENDESK_WRITER: {"zendesk_mock"},
    DbRole.PLANADMIN_READER: {"planadmin_mock"},
    DbRole.PAYROLL_READER: {"payroll_mock"},
    DbRole.PAYROLL_INGEST: {"payroll_mock"},
}
PROBE_TABLE = {
    "app": "app.users",
    "zendesk_mock": "zendesk_mock.tickets",
    "planadmin_mock": "planadmin_mock.plans",
    "payroll_mock": "payroll_mock.payroll_rows",
}


def _scoped(conn: sqlalchemy.Connection, tenants: str) -> None:
    conn.execute(text("SELECT set_config('retirementops.tenant_ids', :v, true)"), {"v": tenants})


@pytest.mark.parametrize("role", list(READABLE), ids=lambda r: r.value)
@pytest.mark.parametrize("schema", list(PROBE_TABLE))
def test_role_privileges_schema_matrix(role_engine: RoleEngine, role: DbRole, schema: str) -> None:
    stmt = text(f"SELECT count(*) FROM {PROBE_TABLE[schema]}")
    if schema in READABLE[role]:
        with role_engine(role).begin() as conn:
            conn.execute(stmt)
    else:
        with (
            pytest.raises(sqlalchemy.exc.ProgrammingError, match="permission denied"),
            role_engine(role).begin() as conn,
        ):
            conn.execute(stmt)


@pytest.mark.parametrize(
    ("role", "stmt"),
    [
        (DbRole.PLANADMIN_READER, "UPDATE planadmin_mock.enrollments SET deferral_pct = 15"),
        (DbRole.PLANADMIN_READER, "DELETE FROM planadmin_mock.participants"),
        (
            DbRole.PAYROLL_READER,
            "UPDATE payroll_mock.payroll_rows SET employee_contribution_cents = 0",
        ),
        (
            DbRole.PAYROLL_INGEST,
            "UPDATE payroll_mock.payroll_rows SET employee_contribution_cents = 0",
        ),
        (DbRole.PAYROLL_INGEST, "DELETE FROM payroll_mock.payroll_rows"),
        (DbRole.ZENDESK_READER, "UPDATE zendesk_mock.tickets SET assignee_queue = 'HUMAN_TRIAGE'"),
        (DbRole.ZENDESK_WRITER, "DELETE FROM zendesk_mock.ticket_comments"),
        (DbRole.ZENDESK_WRITER, "UPDATE zendesk_mock.ticket_comments SET body = 'x'"),
        (DbRole.APP_SERVICE, "UPDATE app.users SET roles = ARRAY['reviewer']"),
    ],
)
def test_role_privileges_no_financial_or_destructive_writes(
    role_engine: RoleEngine, role: DbRole, stmt: str
) -> None:
    with (
        pytest.raises(sqlalchemy.exc.ProgrammingError, match="permission denied"),
        role_engine(role).begin() as conn,
    ):
        _scoped(conn, "TEN-ALDER,TEN-BIRCH,TEN-COBALT")
        conn.execute(text(stmt))


def test_writer_cannot_update_ticket_status(role_engine: RoleEngine, owner_engine: Engine) -> None:
    # Layer 1: no column privilege on status.
    with (
        pytest.raises(sqlalchemy.exc.ProgrammingError, match="permission denied"),
        role_engine(DbRole.ZENDESK_WRITER).begin() as conn,
    ):
        _scoped(conn, "TEN-ALDER")
        conn.execute(
            text("UPDATE zendesk_mock.tickets SET status = 'solved' WHERE ticket_id = 'ZD-1001'")
        )
    # Layer 2: even if someone mistakenly grants the column, the trigger still refuses.
    with owner_engine.begin() as conn:
        conn.execute(text("GRANT UPDATE (status) ON zendesk_mock.tickets TO svc_zendesk_writer"))
    try:
        with (
            pytest.raises(sqlalchemy.exc.DBAPIError, match="only change inside the ticketing"),
            role_engine(DbRole.ZENDESK_WRITER).begin() as conn,
        ):
            _scoped(conn, "TEN-ALDER")
            conn.execute(
                text(
                    "UPDATE zendesk_mock.tickets SET status = 'closed' WHERE ticket_id = 'ZD-1001'"
                )
            )
    finally:
        with owner_engine.begin() as conn:
            conn.execute(
                text("REVOKE UPDATE (status) ON zendesk_mock.tickets FROM svc_zendesk_writer")
            )
    with owner_engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT status FROM zendesk_mock.tickets WHERE ticket_id = 'ZD-1001'")
            ).scalar_one()
            == "new"
        )


# ---------------------------------------------------------------- RLS
RLS_PROBES = [
    (DbRole.ZENDESK_READER, "zendesk_mock.tickets"),
    (DbRole.ZENDESK_READER, "zendesk_mock.ticket_comments"),
    (DbRole.PLANADMIN_READER, "planadmin_mock.tenants"),
    (DbRole.PLANADMIN_READER, "planadmin_mock.plans"),
    (DbRole.PLANADMIN_READER, "planadmin_mock.plan_rules"),
    (DbRole.PLANADMIN_READER, "planadmin_mock.participants"),
    (DbRole.PLANADMIN_READER, "planadmin_mock.enrollments"),
    (DbRole.PAYROLL_READER, "payroll_mock.payroll_files"),
    (DbRole.PAYROLL_READER, "payroll_mock.payroll_rows"),
]


@pytest.mark.parametrize(("role", "table"), RLS_PROBES, ids=lambda x: getattr(x, "value", x))
def test_rls_without_tenant_setting_sees_nothing(
    role_engine: RoleEngine, role: DbRole, table: str
) -> None:
    with role_engine(role).begin() as conn:  # no set_config at all
        assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0
        _scoped(conn, "")
        assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0


@pytest.mark.parametrize(("role", "table"), RLS_PROBES, ids=lambda x: getattr(x, "value", x))
def test_rls_scope_shows_only_that_tenant(
    role_engine: RoleEngine, owner_engine: Engine, role: DbRole, table: str
) -> None:
    with owner_engine.connect() as conn:
        expected = conn.execute(
            text(f"SELECT count(*) FROM {table} WHERE tenant_id = 'TEN-BIRCH'")
        ).scalar_one()
    with role_engine(role).begin() as conn:
        _scoped(conn, "TEN-BIRCH")
        tenants = {r[0] for r in conn.execute(text(f"SELECT DISTINCT tenant_id FROM {table}"))}
        count = conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
    assert tenants <= {"TEN-BIRCH"} and count == expected and expected > 0


def test_rls_setting_does_not_leak_across_pooled_transactions(role_engine: RoleEngine) -> None:
    engine = role_engine(DbRole.PLANADMIN_READER)
    with engine.begin() as conn:
        _scoped(conn, "TEN-ALDER,TEN-BIRCH,TEN-COBALT")
        assert conn.execute(text("SELECT count(*) FROM planadmin_mock.plans")).scalar_one() == 5
    for _ in range(3):  # likely the same pooled connection
        with engine.begin() as conn:
            assert conn.execute(text("SELECT count(*) FROM planadmin_mock.plans")).scalar_one() == 0


def test_rls_blocks_insert_for_another_tenant(role_engine: RoleEngine, dataset: Dataset) -> None:
    alder_file = next(
        f for f in dataset.tables["payroll_mock.payroll_files"] if f["tenant_id"] == "TEN-ALDER"
    )
    with (
        pytest.raises(sqlalchemy.exc.ProgrammingError, match="row-level security"),
        role_engine(DbRole.PAYROLL_INGEST).begin() as conn,
    ):
        _scoped(conn, "TEN-BIRCH")  # scoped to Birchway, tries to write an Alder row
        conn.execute(
            text(
                "INSERT INTO payroll_mock.payroll_rows (file_id, tenant_id, plan_id, pay_period_id, "
                "participant_ref, eligible_pay_cents, employee_contribution_cents, "
                "employer_contribution_cents) VALUES (:f, 'TEN-ALDER', :p, :pp, 'PART-0001', 1, 0, 0)"
            ),
            {
                "f": alder_file["file_id"],
                "p": alder_file["plan_id"],
                "pp": alder_file["pay_period_id"],
            },
        )


def test_writer_connector_cannot_touch_other_tenant_ticket(
    role_engine: RoleEngine, dataset: Dataset
) -> None:
    birch_ticket = next(
        t for t in dataset.tables["zendesk_mock.tickets"] if t["tenant_id"] == "TEN-BIRCH"
    )
    writer = ZendeskMockWriter(role_engine(DbRole.ZENDESK_WRITER))
    with pytest.raises(NotVisibleError):
        writer.apply_assignment(
            TenantScope(frozenset({"TEN-ALDER"})),
            ticket_id=birch_ticket["ticket_id"],
            queue=Queue.HUMAN_TRIAGE,
            expected_version=1,
        )
    with pytest.raises(NotVisibleError):
        writer.post_public_reply(
            TenantScope(frozenset({"TEN-ALDER"})),
            ticket_id=birch_ticket["ticket_id"],
            body="hello",
            idempotency_key="test-key-cross-tenant",
            expected_version=1,
        )
