from __future__ import annotations

from collections.abc import Callable

import pytest
import sqlalchemy.exc
from sqlalchemy import Engine, text

from retirementops.config import DbRole
from retirementops.governance.audit import AuditEvent, AuditLog, verify_chain
from retirementops.services.container import Container
from retirementops.services.context import RequestContext
from retirementops.services.errors import Forbidden, NotFound

pytestmark = pytest.mark.integration

HEADER = "participant_ref,pay_period_id,eligible_pay,employee_contribution,employer_contribution\n"


# ---------------------------------------------------------------- audit chain
def test_audit_append_and_verify(role_engine: Callable[[DbRole], Engine]) -> None:
    log = AuditLog(role_engine(DbRole.APP_SERVICE))
    for i in range(3):
        log.append(AuditEvent("op.alex", "test.event", details={"n": i, "reason": "probe"}))
    report = log.verify()
    assert report.ok and report.checked >= 3


def test_audit_details_reject_raw_content(role_engine: Callable[[DbRole], Engine]) -> None:
    log = AuditLog(role_engine(DbRole.APP_SERVICE))
    for bad in ({"ticket_body": "x"}, {"access_token": "x"}, {"Bad-Key": 1}, {"note": "x" * 501}):
        with pytest.raises(ValueError):
            log.append(AuditEvent("op.alex", "test.event", details=bad))


@pytest.mark.parametrize(
    "stmt",
    [
        "UPDATE app.audit_events SET event_type = 'x.y'",
        "DELETE FROM app.audit_events",
        "TRUNCATE app.audit_events",
    ],
)
def test_audit_update_blocked(
    role_engine: Callable[[DbRole], Engine], owner_engine: Engine, stmt: str
) -> None:
    AuditLog(role_engine(DbRole.APP_SERVICE)).append(AuditEvent("op.alex", "test.event"))
    # The runtime role has no privilege at all...
    with (
        pytest.raises(sqlalchemy.exc.ProgrammingError, match="permission denied"),
        role_engine(DbRole.APP_SERVICE).begin() as conn,
    ):
        conn.execute(text(stmt))
    # ...and even the table owner is stopped by the append-only trigger.
    with (
        pytest.raises(sqlalchemy.exc.DBAPIError, match="append-only"),
        owner_engine.begin() as conn,
    ):
        conn.execute(text(stmt))


def test_chain_verifier_detects_edit(
    role_engine: Callable[[DbRole], Engine], owner_engine: Engine
) -> None:
    """Simulates a privileged insider: the owner disables the trigger and rewrites a row.
    The chain does not prevent this (tamper-EVIDENT, not immutable) but it is detected."""
    log = AuditLog(role_engine(DbRole.APP_SERVICE))
    log.append(AuditEvent("op.alex", "test.before_tamper"))
    with owner_engine.begin() as conn:
        seq = conn.execute(text("SELECT max(seq) FROM app.audit_events")).scalar_one()
        conn.execute(text("ALTER TABLE app.audit_events DISABLE TRIGGER audit_no_update_delete"))
        conn.execute(
            text("UPDATE app.audit_events SET actor_principal = 'someone.else' WHERE seq = :s"),
            {"s": seq},
        )
        with owner_engine.connect() as c2:  # a reader outside this transaction still sees old data
            assert verify_chain(c2).ok
        report = verify_chain(conn)
        conn.execute(
            text("UPDATE app.audit_events SET actor_principal = 'op.alex' WHERE seq = :s"),
            {"s": seq},
        )
        conn.execute(text("ALTER TABLE app.audit_events ENABLE TRIGGER audit_no_update_delete"))
    assert not report.ok and report.first_bad_seq == seq
    assert log.verify().ok  # restored


# ---------------------------------------------------------------- payroll ingest
def _counts(owner_engine: Engine) -> tuple[int, int]:
    with owner_engine.connect() as conn:
        return (
            conn.execute(text("SELECT count(*) FROM payroll_mock.payroll_files")).scalar_one(),
            conn.execute(text("SELECT count(*) FROM payroll_mock.payroll_rows")).scalar_one(),
        )


def test_upload_with_ssn_column_rejected_and_not_stored(
    container: Container, ctx_for: Callable[[str], RequestContext], owner_engine: Engine
) -> None:
    before = _counts(owner_engine)
    data = (
        HEADER.strip() + ",ssn\nPART-0001,PP-2026-08,2000.00,100.00,50.00,000-12-3456\n"
    ).encode()
    res = container.payroll_ingest.ingest(
        ctx_for("op.alex"),
        plan_id="PLAN-001",
        pay_period_id="PP-2026-08",
        filename="alder_pp08.csv",
        data=data,
    )
    assert not res.accepted and res.reason == "RESTRICTED_FIELD"
    assert _counts(owner_engine) == before  # nothing persisted
    with owner_engine.connect() as conn:
        rec = conn.execute(
            text(
                "SELECT details::text FROM app.audit_events WHERE event_type = "
                "'payroll.upload_rejected' ORDER BY seq DESC LIMIT 1"
            )
        ).scalar_one()
    assert "000-12-3456" not in rec and "PART-0001" not in rec and "alder_pp08" not in rec
    assert "RESTRICTED_FIELD" in rec and "filename_hash" in rec


def test_upload_with_foreign_participant_rejected(
    container: Container, ctx_for: Callable[[str], RequestContext], owner_engine: Engine
) -> None:
    before = _counts(owner_engine)
    data = (
        HEADER + "PART-0030,PP-2026-08,2000.00,100.00,50.00\n"
    ).encode()  # PART-0030 is Birchway
    res = container.payroll_ingest.ingest(
        ctx_for("op.alex"),
        plan_id="PLAN-001",
        pay_period_id="PP-2026-08",
        filename="x.csv",
        data=data,
    )
    assert not res.accepted and res.reason == "INVALID_ROW"
    assert _counts(owner_engine) == before


def test_valid_upload_is_stored_atomically(
    container: Container, ctx_for: Callable[[str], RequestContext], owner_engine: Engine
) -> None:
    # PART-0011 is NOT_ENROLLED and unused by other assertions for PP-2026-08.
    before = _counts(owner_engine)
    data = (HEADER + "PART-0011,PP-2026-08,10.00,0.00,0.00\n").encode()
    res = container.payroll_ingest.ingest(
        ctx_for("op.alex"),
        plan_id="PLAN-001",
        pay_period_id="PP-2026-08",
        filename="ok.csv",
        data=data,
    )
    assert res.accepted and res.file_id and res.file_id.startswith("PF-")
    assert _counts(owner_engine) == (before[0] + 1, before[1] + 1)


def test_ingest_requires_scope_and_visible_plan(
    container: Container, ctx_for: Callable[[str], RequestContext]
) -> None:
    with pytest.raises(Forbidden):  # reviewers have no payroll:ingest
        container.payroll_ingest.ingest(
            ctx_for("rev.sam"),
            plan_id="PLAN-001",
            pay_period_id="PP-2026-08",
            filename="x",
            data=b"",
        )
    with pytest.raises(NotFound):  # op.casey is Cobalt-only
        container.payroll_ingest.ingest(
            ctx_for("op.casey"),
            plan_id="PLAN-001",
            pay_period_id="PP-2026-08",
            filename="x",
            data=b"",
        )
