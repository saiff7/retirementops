"""M3: `svc_agent_recorder` and the run-trace tables (migration 0007).

Proves, against Postgres directly:
- the role holds INSERT and SELECT on the three trace tables and nothing else, anywhere;
- no other runtime role (app_service included) can touch the trace tables;
- RLS isolates tenants on read and write;
- trace rows cannot be attached to another tenant's run, or to a run written in another transaction;
- trace rows are immutable for every role, the owner included.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import sqlalchemy.exc
from sqlalchemy import Connection, Engine, text

from retirementops.config import RUNTIME_ROLES, DbRole
from retirementops.domain.authz import TenantScope
from retirementops.store.traces import (
    EvidenceTrace,
    RunHeader,
    RunTraceStore,
    ToolCallTrace,
)

pytestmark = pytest.mark.integration
RoleEngine = Callable[[DbRole], Engine]
TRACE_TABLES = ("agent_runs", "tool_calls", "evidence_items")
R = DbRole.AGENT_RECORDER
NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
H = "a" * 64


def _run_id() -> str:
    return f"RUN-{secrets.token_hex(8).upper()}"


def _header(run_id: str, tenant: str = "TEN-ALDER") -> RunHeader:
    return RunHeader(
        run_id=run_id,
        tenant_id=tenant,
        ticket_id="ZD-1001",
        started_by="op.alex",
        correlation_id="test-corr",
        skill_name="service-ticket-triage",
        skill_version="service-ticket-triage@1.0.0",
        skill_sha256=H,
        provider="scripted",
        model_name="scripted-good",
        model_digest=None,
        final_state="PENDING_REVIEW",
        state_path=(
            "RECEIVED",
            "CLASSIFIED",
            "RESOLVED",
            "EVIDENCE_GATHERED",
            "DRAFTED",
            "PENDING_REVIEW",
        ),
        state_reasons=(),
        intent="CONTRIBUTION_DISCREPANCY",
        confidence=Decimal("0.950"),
        routed_queue="PAYROLL_OPERATIONS",
        routing_rules_version="routing-rules/1",
        requires_manual_review=False,
        finding_status="DISCREPANCY",
        draft_id=None,
        proposal_id=None,
        model_turns=3,
        started_at=NOW,
        finished_at=NOW,
    )


CALL = ToolCallTrace(1, "get_ticket_details", "allow", "ok", H, 25, H, Decimal("1.25"), NOW)
EVIDENCE = EvidenceTrace(1, "zendesk_mock", "ticket", "ZD-1001", "1", H, NOW)


@pytest.fixture
def store(role_engine: RoleEngine) -> RunTraceStore:
    return RunTraceStore(role_engine(R))


@pytest.fixture
def recorder_tx(role_engine: RoleEngine) -> Iterator[Connection]:
    conn = role_engine(R).connect()
    tx = conn.begin()
    conn.execute(text("SELECT set_config('retirementops.tenant_ids', 'TEN-ALDER', true)"))
    try:
        yield conn
    finally:
        tx.rollback()
        conn.close()


# ---------------------------------------------------------------- role and privileges
def test_recorder_role_cannot_escalate(owner_engine: Engine) -> None:
    with owner_engine.connect() as c:
        row = c.execute(
            text(
                "SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, rolinherit, rolreplication "
                "FROM pg_roles WHERE rolname = 'svc_agent_recorder'"
            )
        ).one()
        member_of = c.execute(
            text(
                "SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member "
                "WHERE r.rolname = 'svc_agent_recorder'"
            )
        ).scalar_one()
    assert tuple(row) == (False, False, False, False, False, False)
    assert member_of == 0


def test_recorder_holds_only_select_and_insert_on_the_trace_tables(owner_engine: Engine) -> None:
    """Exhaustive: every table and privilege in every project schema."""
    with owner_engine.connect() as c:
        tables = [
            r[0]
            for r in c.execute(
                text(
                    "SELECT schemaname || '.' || tablename FROM pg_tables WHERE schemaname IN "
                    "('app', 'sec', 'zendesk_mock', 'planadmin_mock', 'payroll_mock', 'servicekb_mock')"
                )
            )
        ]
        granted = {
            (t, p)
            for t in tables
            for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
            if c.execute(
                text("SELECT has_table_privilege('svc_agent_recorder', :t, :p)"), {"t": t, "p": p}
            ).scalar()
        }
        owns = c.execute(
            text(
                "SELECT count(*) FROM pg_class cl JOIN pg_roles r ON r.oid = cl.relowner "
                "WHERE r.rolname = 'svc_agent_recorder'"
            )
        ).scalar_one()
        can_create = c.execute(
            text("SELECT has_schema_privilege('svc_agent_recorder', 'app', 'CREATE')")
        ).scalar()
    assert granted == {(f"app.{t}", p) for t in TRACE_TABLES for p in ("SELECT", "INSERT")}
    assert owns == 0 and can_create is False


@pytest.mark.parametrize("role", [r for r in RUNTIME_ROLES if r is not R], ids=lambda r: r.value)
def test_no_other_runtime_role_can_touch_trace_tables(owner_engine: Engine, role: DbRole) -> None:
    with owner_engine.connect() as c:
        for t in TRACE_TABLES:
            for p in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                assert not c.execute(
                    text("SELECT has_table_privilege(:r, :t, :p)"),
                    {"r": role.value, "t": f"app.{t}", "p": p},
                ).scalar(), (role, t, p)


@pytest.mark.parametrize(
    "stmt",
    [
        "SELECT count(*) FROM app.users",
        "SELECT count(*) FROM app.drafts",
        "SELECT count(*) FROM app.audit_events",
        "SELECT count(*) FROM zendesk_mock.tickets",
        "SELECT count(*) FROM payroll_mock.payroll_rows",
        "INSERT INTO app.drafts (draft_id) VALUES ('DRF-0000000000000000')",
        "CREATE TABLE app.sneaky (x int)",
    ],
)
def test_recorder_cannot_reach_unrelated_data(role_engine: RoleEngine, stmt: str) -> None:
    with (
        pytest.raises(sqlalchemy.exc.ProgrammingError, match="permission denied"),
        role_engine(R).begin() as conn,
    ):
        conn.execute(text(stmt))


# ---------------------------------------------------------------- write, read, RLS
def test_store_writes_and_reads_one_run_atomically(store: RunTraceStore) -> None:
    run_id = _run_id()
    store.write_run(_header(run_id), [CALL], [EVIDENCE])
    got = store.get_run(TenantScope(frozenset({"TEN-ALDER"})), run_id)
    assert got is not None
    assert got.header["final_state"] == "PENDING_REVIEW" and got.header["tool_call_count"] == 1
    assert [c["tool_name"] for c in got.tool_calls] == ["get_ticket_details"]
    assert [e["record_id"] for e in got.evidence] == ["ZD-1001"]
    assert store.get_run(TenantScope(frozenset({"TEN-BIRCH"})), run_id) is None  # RLS + filter


def test_failed_write_leaves_nothing_behind(store: RunTraceStore, owner_engine: Engine) -> None:
    run_id = _run_id()
    bad = ToolCallTrace(1, "BAD NAME", "allow", "ok", H, 1, None, Decimal("0"), NOW)
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        store.write_run(_header(run_id), [bad], [])
    with owner_engine.connect() as c:
        n = c.execute(
            text("SELECT count(*) FROM app.agent_runs WHERE run_id = :r"), {"r": run_id}
        ).scalar_one()
    assert n == 0


def test_rls_hides_and_refuses_other_tenants(role_engine: RoleEngine, store: RunTraceStore) -> None:
    store.write_run(_header(_run_id(), "TEN-BIRCH"), [CALL], [EVIDENCE])
    with role_engine(R).begin() as conn:  # no tenant scope at all
        assert conn.execute(text("SELECT count(*) FROM app.agent_runs")).scalar_one() == 0
    with role_engine(R).begin() as conn:
        conn.execute(text("SELECT set_config('retirementops.tenant_ids', 'TEN-ALDER', true)"))
        assert (
            conn.execute(
                text("SELECT count(*) FROM app.agent_runs WHERE tenant_id = 'TEN-BIRCH'")
            ).scalar_one()
            == 0
        )
    with (
        pytest.raises(sqlalchemy.exc.ProgrammingError, match="row-level security"),
        role_engine(R).begin() as conn,
    ):
        conn.execute(text("SELECT set_config('retirementops.tenant_ids', 'TEN-ALDER', true)"))
        conn.execute(
            text(
                "INSERT INTO app.agent_runs SELECT * FROM jsonb_populate_record("
                "NULL::app.agent_runs, CAST(:j AS jsonb))"
            ),
            {"j": _json_header(_run_id(), "TEN-BIRCH")},
        )


def _json_header(run_id: str, tenant: str) -> str:
    import json

    h = _header(run_id, tenant)
    row = {f: getattr(h, f) for f in RunHeader.__slots__}
    row |= {
        "state_path": list(h.state_path),
        "state_reasons": [],
        "tool_call_count": 0,
        "confidence": str(h.confidence),
        "started_at": NOW.isoformat(),
        "finished_at": NOW.isoformat(),
    }
    return json.dumps(row)


# ---------------------------------------------------------------- run boundaries
def _insert_call(conn: Connection, run_id: str, tenant: str = "TEN-ALDER", seq: int = 1) -> None:
    conn.execute(
        text(
            "INSERT INTO app.tool_calls VALUES (:r, :t, :s, 'get_ticket_details', 'allow', 'ok', "
            ":h, 1, NULL, 0, now())"
        ),
        {"r": run_id, "t": tenant, "s": seq, "h": H},
    )


def test_trace_rows_cannot_be_added_to_a_finished_run(
    store: RunTraceStore, recorder_tx: Connection
) -> None:
    run_id = _run_id()
    store.write_run(_header(run_id), [CALL], [])  # committed in its own transaction
    with pytest.raises(sqlalchemy.exc.DBAPIError, match="same transaction as their run"):
        _insert_call(recorder_tx, run_id, seq=2)


def test_trace_rows_cannot_reference_a_missing_run(recorder_tx: Connection) -> None:
    with pytest.raises(sqlalchemy.exc.DBAPIError, match="same transaction as their run"):
        _insert_call(recorder_tx, _run_id())


def test_trace_rows_cannot_be_attached_to_another_tenants_run(
    store: RunTraceStore, recorder_tx: Connection
) -> None:
    birch = _run_id()
    store.write_run(_header(birch, "TEN-BIRCH"), [], [])
    # Labelled as our tenant but pointing at Birch's run: the guard and FK both refuse it.
    with pytest.raises(sqlalchemy.exc.DBAPIError):
        _insert_call(recorder_tx, birch, "TEN-ALDER")


@pytest.mark.parametrize(
    "stmt",
    [
        "UPDATE app.agent_runs SET final_state = 'PENDING_REVIEW'",
        "DELETE FROM app.tool_calls",
        "TRUNCATE app.evidence_items",
    ],
)
def test_recorder_cannot_modify_or_delete_traces(role_engine: RoleEngine, stmt: str) -> None:
    with (
        pytest.raises(sqlalchemy.exc.ProgrammingError, match="permission denied"),
        role_engine(R).begin() as conn,
    ):
        conn.execute(text("SELECT set_config('retirementops.tenant_ids', 'TEN-ALDER', true)"))
        conn.execute(text(stmt))


@pytest.mark.parametrize(
    "stmt",
    [
        "UPDATE app.agent_runs SET final_state = 'PENDING_REVIEW'",
        "DELETE FROM app.tool_calls",
        "TRUNCATE app.evidence_items",
    ],
)
def test_even_the_owner_cannot_rewrite_traces(
    owner_engine: Engine, store: RunTraceStore, stmt: str
) -> None:
    store.write_run(_header(_run_id()), [CALL], [EVIDENCE])
    with pytest.raises(sqlalchemy.exc.DBAPIError, match="immutable"), owner_engine.begin() as c:
        c.execute(text(stmt))
