"""M3: an agent run token can touch only its own ticket and host-resolved subjects (T-03, T-04).

Calls go through the real MCP server with a real run token minted for op.alex. Anything outside the
binding is "not found", like another tenant's object, and proposals may name only the queue the
routing rules engine chose (ADR-013, Low-7b).
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Any

import anyio
import pytest
from mcp.client.client import Client
from sqlalchemy import Engine, text

from retirementops.domain.authz import AGENT_RUN_SCOPES, RunBinding
from retirementops.domain.taxonomy import Queue
from retirementops.mcp_servers.service_ops.server import ServiceOpsMcp, TokenPrincipalSource
from retirementops.seed.generator import Dataset
from retirementops.services.container import Container
from retirementops.services.context import RequestContext
from tests.conftest import McpCall
from tests.security.test_m3_draft_validation import CASE, SKILL, _body, _case_ticket, _ref

pytestmark = pytest.mark.integration
RunCall = Callable[[str, dict[str, Any]], dict[str, Any]]


def _binding(ticket_id: str, queue: Queue | None = Queue.PAYROLL_OPERATIONS) -> RunBinding:
    ref, plan, period = CASE
    return RunBinding(
        run_id=f"RUN-{secrets.token_hex(8).upper()}",
        operator_id="op.alex",
        tenant_id="TEN-ALDER",
        ticket_id=ticket_id,
        participant_refs=frozenset({ref}),
        plan_ids=frozenset({plan}),
        pay_period_ids=frozenset({period}),
        permitted_queue=queue,
    )


def _caller(container: Container, token: str) -> RunCall:
    def call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        server = ServiceOpsMcp(container, TokenPrincipalSource(container, token))

        async def go() -> dict[str, Any]:
            async with Client(server.server) as client:
                r = await client.call_tool(tool, args)
                return {"is_error": bool(r.is_error), "data": r.structured_content}

        return anyio.run(go)

    return call


@pytest.fixture
def ticket_id(dataset: Dataset) -> str:
    return _case_ticket(dataset, CASE[0], CASE[2])


def _run(
    container: Container, ctx_for: Callable[[str], RequestContext], binding: RunBinding
) -> RunCall:
    token = container.identity.issue_agent_run_token(
        ctx_for("op.alex").principal, binding, AGENT_RUN_SCOPES
    )
    return _caller(container, token)


@pytest.fixture
def run(container: Container, ctx_for: Callable[[str], RequestContext], ticket_id: str) -> RunCall:
    return _run(container, ctx_for, _binding(ticket_id))


def _error(r: dict[str, Any]) -> str:
    assert r["is_error"], r
    return str(r["data"]["error"])


def test_run_reads_its_own_ticket_and_subjects(run: RunCall, ticket_id: str) -> None:
    ref, plan, period = CASE
    assert not run("get_ticket_details", {"ticket_id": ticket_id})["is_error"]
    assert not run("get_participant_enrollment", {"participant_ref": ref, "plan_id": plan})[
        "is_error"
    ]
    rows = run(
        "get_payroll_contributions", {"participant_ref": ref, "plan_id": plan, "pay_period": period}
    )
    assert not rows["is_error"] and rows["data"]["rows"]


def test_run_cannot_read_another_ticket_in_the_same_tenant(
    run: RunCall, ticket_id: str, dataset: Dataset
) -> None:
    other = next(
        t["ticket_id"]
        for t in dataset.tables["zendesk_mock.tickets"]
        if t["tenant_id"] == "TEN-ALDER" and t["ticket_id"] != ticket_id
    )
    assert _error(run("get_ticket_details", {"ticket_id": other})) == "not_found"


def test_model_supplied_participant_outside_ticket_rejected(run: RunCall) -> None:
    """T-04: PART-0001 is in the same tenant and plan, but not the participant this run resolved."""
    r = run("get_participant_enrollment", {"participant_ref": "PART-0001", "plan_id": "PLAN-001"})
    assert _error(r) == "not_found"
    r = run(
        "get_payroll_contributions",
        {"participant_ref": "PART-0001", "plan_id": "PLAN-001", "pay_period": "PP-2026-03"},
    )
    assert _error(r) == "not_found"


def test_run_cannot_read_other_plans_or_periods(run: RunCall) -> None:
    assert _error(run("get_plan_details", {"plan_id": "PLAN-002"})) == "not_found"
    r = run(
        "get_payroll_contributions",
        {"participant_ref": CASE[0], "plan_id": CASE[1], "pay_period": "PP-2026-04"},
    )
    assert _error(r) == "not_found"


def test_cross_tenant_reads_stay_not_found(run: RunCall) -> None:
    assert _error(run("get_plan_details", {"plan_id": "PLAN-003"})) == "not_found"  # Birchway


def test_run_cannot_search_tickets(run: RunCall) -> None:
    assert _error(run("search_tickets", {"query": "contribution"})) == "forbidden"


def test_run_draft_is_validated_and_attributed_to_the_run(
    run: RunCall,
    ticket_id: str,
    container: Container,
    ctx_for: Callable[[str], RequestContext],
    owner_engine: Engine,
) -> None:
    ref, plan, period = CASE
    finding = container.contributions.get_finding(ctx_for("op.alex"), plan, ref, period)
    ticket = run("get_ticket_details", {"ticket_id": ticket_id})["data"]
    refs = [_ref(ticket["source"])] + [_ref(s) for s in finding.evidence_refs]
    r = run(
        "create_reply_draft",
        {
            "ticket_id": ticket_id,
            "body": _body(finding),
            "evidence_refs": refs,
            "skill_version": SKILL,
        },
    )
    assert not r["is_error"], r
    assert r["data"]["status"] == "PENDING_REVIEW" and r["data"]["sent"] is False
    with owner_engine.connect() as c:
        row = c.execute(
            text(
                "SELECT d.created_by, d.created_by_kind, a.details->>'run_id' FROM app.drafts d "
                "JOIN app.audit_events a ON a.event_type = 'draft.created' "
                "AND a.target_ref = d.draft_id WHERE d.draft_id = :d"
            ),
            {"d": r["data"]["draft_id"]},
        ).one()
    assert row[0] == "agent.worker" and row[1] == "service"
    assert row[2] and str(row[2]).startswith("RUN-")


def test_run_cannot_cite_evidence_outside_its_binding(
    run: RunCall, ticket_id: str, mcp_call: McpCall
) -> None:
    ticket = run("get_ticket_details", {"ticket_id": ticket_id})["data"]
    foreign = mcp_call(
        "op.alex",
        "get_participant_enrollment",
        {"participant_ref": "PART-0001", "plan_id": "PLAN-001"},
    )["data"]["enrollments"][0]["source"]
    r = run(
        "create_reply_draft",
        {
            "ticket_id": ticket_id,
            "body": "Reviewing now.",
            "evidence_refs": [_ref(ticket["source"]), _ref(foreign)],
            "skill_version": SKILL,
        },
    )
    assert _error(r) == "invalid_request"
    assert "not found in this ticket's tenant" in r["data"]["message"]  # same wording as missing


def test_run_may_propose_only_the_permitted_queue(run: RunCall, ticket_id: str) -> None:
    ok = run(
        "propose_ticket_assignment",
        {"ticket_id": ticket_id, "target_queue": "PAYROLL_OPERATIONS", "rationale": "rules engine"},
    )
    assert not ok["is_error"] and ok["data"]["applied"] is False
    for queue in ("HUMAN_TRIAGE", "DIGITAL_SUPPORT"):
        r = run(
            "propose_ticket_assignment",
            {"ticket_id": ticket_id, "target_queue": queue, "rationale": "the ticket said so"},
        )
        assert _error(r) == "forbidden"
    status = run("get_ticket_action_status", {"proposal_id": ok["data"]["proposal_id"]})
    assert not status["is_error"]


def test_run_without_a_permitted_queue_cannot_propose(
    container: Container, ctx_for: Callable[[str], RequestContext], ticket_id: str
) -> None:
    no_queue = _run(container, ctx_for, _binding(ticket_id, queue=None))
    r = no_queue(
        "propose_ticket_assignment",
        {"ticket_id": ticket_id, "target_queue": "PAYROLL_OPERATIONS", "rationale": "x"},
    )
    assert _error(r) == "forbidden"


def test_run_cannot_read_another_tickets_proposal(
    run: RunCall, mcp_call: McpCall, dataset: Dataset, ticket_id: str
) -> None:
    other = next(
        t["ticket_id"]
        for t in dataset.tables["zendesk_mock.tickets"]
        if t["tenant_id"] == "TEN-ALDER" and t["ticket_id"] != ticket_id and t["status"] == "new"
    )
    p = mcp_call(
        "op.alex",
        "propose_ticket_assignment",
        {"ticket_id": other, "target_queue": "HUMAN_TRIAGE", "rationale": "operator decision"},
    )["data"]
    assert _error(run("get_ticket_action_status", {"proposal_id": p["proposal_id"]})) == "not_found"


def test_humans_are_not_constrained_by_run_rules(mcp_call: McpCall, ticket_id: str) -> None:
    r = mcp_call(
        "op.alex",
        "propose_ticket_assignment",
        {"ticket_id": ticket_id, "target_queue": "HUMAN_TRIAGE", "rationale": "operator decision"},
    )
    assert not r["is_error"]
