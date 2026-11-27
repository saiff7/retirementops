"""M3: the server-side validator decides PENDING_REVIEW vs NEEDS_MANUAL_REVIEW (ADR-013, T-07, T-08).

Drafts are created through the real MCP server. Ground truth comes from the seeded ONE_CENT_UNDER case
(PART-0002, PLAN-001, PP-2026-03: employee deferral 1 cent short) and is recomputed by the server.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
import sqlalchemy.exc
from sqlalchemy import Connection, Engine, text

from retirementops.config import DbRole
from retirementops.domain.contributions import ContributionFinding, FindingStatus
from retirementops.domain.draft_validator import VALIDATOR_VERSION
from retirementops.domain.money import Money, format_money
from retirementops.domain.records import ContributionType
from retirementops.seed.generator import Dataset
from retirementops.services.container import Container
from retirementops.services.context import RequestContext
from tests.conftest import McpCall

pytestmark = pytest.mark.integration
RoleEngine = Callable[[DbRole], Engine]
SKILL = "service-ticket-triage@0.1.0"
CASE = ("PART-0002", "PLAN-001", "PP-2026-03")
H = "0" * 64


def _ref(source: Any) -> dict[str, str]:
    get = source.get if isinstance(source, dict) else lambda k: getattr(source, k)
    return {k: get(k) for k in ("system", "record_type", "record_id", "content_hash")}


def _case_ticket(dataset: Dataset, ref: str, period: str) -> str:
    return next(
        lab["ticket_id"]
        for lab in dataset.ticket_labels
        if lab["participant_ref"] == ref
        and lab["pay_period_id"] == period
        and not lab["flags"]
        and lab["intent"] == "CONTRIBUTION_DISCREPANCY"
    )


@pytest.fixture
def bundle(
    container: Container,
    ctx_for: Callable[[str], RequestContext],
    dataset: Dataset,
    mcp_call: McpCall,
) -> tuple[str, ContributionFinding, list[dict[str, str]]]:
    ref, plan, period = CASE
    finding = container.contributions.get_finding(ctx_for("op.alex"), plan, ref, period)
    assert finding.status is FindingStatus.DISCREPANCY
    tid = _case_ticket(dataset, ref, period)
    ticket = mcp_call("op.alex", "get_ticket_details", {"ticket_id": tid})["data"]
    return tid, finding, [_ref(ticket["source"])] + [_ref(s) for s in finding.evidence_refs]


def _body(f: ContributionFinding, expected: str | None = None) -> str:
    ee = next(c for c in f.components if c.component is ContributionType.EMPLOYEE_DEFERRAL)
    gap = format_money(Money(abs(ee.variance.amount)))
    return (
        f"For participant {f.participant_ref} in plan {f.plan_id}, pay period {f.pay_period_id} "
        f"(ending {f.determination_date}), the payroll file reports an employee contribution of "
        f"{format_money(ee.reported)}. The expected amount under the plan's fictional rule is "
        f"{expected or format_money(ee.expected)}, a difference of {gap}. Our payroll operations "
        "team will review this finding with you."
    )


def _draft(mcp_call: McpCall, tid: str, body: str, refs: list[dict[str, str]]) -> dict[str, Any]:
    r = mcp_call(
        "op.alex",
        "create_reply_draft",
        {"ticket_id": tid, "body": body, "evidence_refs": refs, "skill_version": SKILL},
    )
    assert not r["is_error"], r["text"]
    return r["data"]  # type: ignore[no-any-return]


def test_validated_draft_is_stored_pending_review(
    mcp_call: McpCall, owner_engine: Engine, bundle: Any
) -> None:
    tid, finding, refs = bundle
    d = _draft(mcp_call, tid, _body(finding), refs)
    assert d["status"] == "PENDING_REVIEW", d
    assert d["status_reasons"] == [] and d["sent"] is False
    assert d["validator"] == {"version": VALIDATOR_VERSION, "passed": True, "issues": []}
    with owner_engine.connect() as c:
        row = c.execute(
            text("SELECT status, validator_version FROM app.drafts WHERE draft_id = :d"),
            {"d": d["draft_id"]},
        ).one()
    assert tuple(row) == ("PENDING_REVIEW", VALIDATOR_VERSION)


def test_invented_amount_goes_to_manual_review_without_echoing_it(
    mcp_call: McpCall, bundle: Any
) -> None:
    tid, finding, refs = bundle
    d = _draft(mcp_call, tid, _body(finding, expected="$999.99"), refs)
    assert d["status"] == "NEEDS_MANUAL_REVIEW"
    assert "UNSUPPORTED_AMOUNT" in d["validator"]["issues"]
    assert "999.99" not in " ".join(d["status_reasons"])


def test_draft_missing_calculation_evidence_goes_to_manual_review(
    mcp_call: McpCall, bundle: Any
) -> None:
    tid, finding, refs = bundle
    partial = [r for r in refs if r["record_type"] != "enrollment"]
    d = _draft(mcp_call, tid, _body(finding), partial)
    assert d["status"] == "NEEDS_MANUAL_REVIEW"
    assert d["validator"]["issues"] == ["EVIDENCE_INCOMPLETE"]


def test_evidence_about_two_participants_goes_to_manual_review(
    mcp_call: McpCall, bundle: Any
) -> None:
    tid, finding, refs = bundle
    other = mcp_call(
        "op.alex",
        "get_participant_enrollment",
        {"participant_ref": "PART-0001", "plan_id": "PLAN-001"},
    )["data"]["enrollments"][0]["source"]
    d = _draft(mcp_call, tid, _body(finding), [*refs, _ref(other)])
    assert d["status"] == "NEEDS_MANUAL_REVIEW"
    assert "EVIDENCE_MIXED_SUBJECTS" in d["validator"]["issues"]


def test_blocking_finding_goes_to_manual_review(
    mcp_call: McpCall,
    container: Container,
    ctx_for: Callable[[str], RequestContext],
    dataset: Dataset,
) -> None:
    finding = container.contributions.get_finding(
        ctx_for("op.alex"), "PLAN-001", "PART-0010", "PP-2026-07"
    )
    assert finding.status is FindingStatus.MISSING_PAYROLL_ROW
    tid = _case_ticket(dataset, "PART-0010", "PP-2026-07")
    ticket = mcp_call("op.alex", "get_ticket_details", {"ticket_id": tid})["data"]
    refs = [_ref(ticket["source"])] + [_ref(s) for s in finding.evidence_refs]
    body = "We are looking into the contribution for PART-0010 in PP-2026-07."
    d = _draft(mcp_call, tid, body, refs)
    assert d["status"] == "NEEDS_MANUAL_REVIEW"
    assert d["validator"]["issues"] == ["FINDING_BLOCKS_DRAFT"]


def test_amounts_without_calculation_evidence_are_refused(mcp_call: McpCall, bundle: Any) -> None:
    tid, _, refs = bundle
    d = _draft(mcp_call, tid, "The expected contribution is $100.00.", refs[:1])  # ticket only
    assert d["status"] == "NEEDS_MANUAL_REVIEW"
    assert "UNSUPPORTED_AMOUNT" in d["validator"]["issues"]


# ---------------------------------------------------------------- database guard (migration 0006)
@pytest.fixture
def app_tx(role_engine: RoleEngine) -> Iterator[Connection]:
    conn = role_engine(DbRole.APP_SERVICE).connect()
    tx = conn.begin()
    conn.execute(text("SELECT set_config('retirementops.tenant_ids', 'TEN-ALDER', true)"))
    try:
        yield conn
    finally:
        tx.rollback()
        conn.close()


def _insert(conn: Connection, status: str, reasons: str, validator: str | None) -> None:
    conn.execute(
        text(
            "INSERT INTO app.drafts (draft_id, ticket_id, tenant_id, status, status_reasons, "
            "created_by, created_at, created_by_kind, validator_version) VALUES "
            "('DRF-00000000000000B1', 'ZD-1001', 'TEN-ALDER', :s, CAST(:r AS jsonb), 'op.alex', "
            "now(), 'human', :v)"
        ),
        {"s": status, "r": reasons, "v": validator},
    )


def test_db_allows_pending_review_only_with_a_passing_validator(app_tx: Connection) -> None:
    _insert(app_tx, "PENDING_REVIEW", "[]", VALIDATOR_VERSION)


@pytest.mark.parametrize(
    ("reasons", "validator"),
    [("[]", None), ('["UNSUPPORTED_AMOUNT: x"]', VALIDATOR_VERSION)],
)
def test_db_refuses_pending_review_without_a_passing_validator(
    app_tx: Connection, reasons: str, validator: str | None
) -> None:
    with pytest.raises(sqlalchemy.exc.DBAPIError, match="requires a passing validator"):
        _insert(app_tx, "PENDING_REVIEW", reasons, validator)


def test_db_still_allows_needs_manual_review_with_reasons(app_tx: Connection) -> None:
    _insert(app_tx, "NEEDS_MANUAL_REVIEW", '["UNSUPPORTED_AMOUNT: x"]', VALIDATOR_VERSION)


def test_db_rejects_malformed_validator_version(app_tx: Connection) -> None:
    with pytest.raises(sqlalchemy.exc.DBAPIError, match="validator_version"):
        _insert(app_tx, "PENDING_REVIEW", "[]", "anything goes")
