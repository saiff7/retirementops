"""M3 acceptance (implementation-plan.md): the worker, end to end, with the ScriptedProvider.

1. One seeded discrepancy ticket runs end to end to PENDING_REVIEW with a correct finding.
2. Scripted adversarial runs (invented amount, cross-tenant call, unlisted tool, close-ticket
   request, malformed JSON, timeout) each end in NEEDS_MANUAL_REVIEW.
3. No connector write happens during any run (mock-system snapshots before and after).
Plus: routing, blocking findings, budgets, the kill switch, traces and audit.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, text

from retirementops.agent_runtime.orchestrator import Orchestrator, RunBudgets, RunOutcome
from retirementops.agent_runtime.providers import scripts
from retirementops.agent_runtime.providers.contracts import ToolCall, ToolDecision
from retirementops.agent_runtime.providers.scripted import Script, ScriptedProvider
from retirementops.agent_runtime.skills import load_skill
from retirementops.domain.contributions import ContributionType
from retirementops.domain.money import format_money
from retirementops.domain.run_state import RunState
from retirementops.seed.generator import Dataset
from retirementops.services.container import Container
from retirementops.services.context import RequestContext
from retirementops.services.errors import NotFound

pytestmark = pytest.mark.integration
SNAPSHOT = {
    "tickets": "SELECT md5(string_agg(ticket_id || status || coalesce(assignee_queue, '') || "
    "version, ',' ORDER BY ticket_id)) FROM zendesk_mock.tickets",
    "comments": "SELECT count(*) FROM zendesk_mock.ticket_comments",
    "events": "SELECT count(*) FROM zendesk_mock.ticket_events",
    "enrollments": "SELECT md5(string_agg(t::text, ',' ORDER BY enrollment_id)) "
    "FROM planadmin_mock.enrollments t",
    "rules": "SELECT md5(string_agg(t::text, ',' ORDER BY rule_id)) FROM planadmin_mock.plan_rules t",
    "payroll": "SELECT md5(string_agg(t::text, ',' ORDER BY file_id, row_id)) "
    "FROM payroll_mock.payroll_rows t",
}


def _snapshot(owner: Engine) -> dict[str, Any]:
    with owner.connect() as c:
        return {k: c.execute(text(q)).scalar() for k, q in SNAPSHOT.items()}


@pytest.fixture
def no_connector_writes(owner_engine: Engine) -> Iterator[None]:
    before = _snapshot(owner_engine)
    yield
    assert _snapshot(owner_engine) == before, "a run wrote to a mock system"


def _label(dataset: Dataset, **match: Any) -> dict[str, Any]:
    return next(
        lab
        for lab in dataset.ticket_labels
        if all(lab.get(k) == v for k, v in match.items())
        and not any(f.startswith("DUPLICATE_OF") for f in lab["flags"])
    )


def _run(
    container: Container,
    ctx_for: Callable[[str], RequestContext],
    script: Script,
    ticket_id: str,
    operator: str = "op.alex",
    **kw: Any,
) -> RunOutcome:
    return Orchestrator(container, ScriptedProvider(script), **kw).run(ctx_for(operator), ticket_id)


@pytest.fixture
def discrepancy(dataset: Dataset) -> dict[str, Any]:
    return _label(dataset, participant_ref="PART-0002", pay_period_id="PP-2026-03", flags=[])


# ---------------------------------------------------------------- acceptance 1
def test_seeded_discrepancy_runs_end_to_end_to_pending_review(
    container: Container,
    ctx_for: Callable[[str], RequestContext],
    discrepancy: dict[str, Any],
    owner_engine: Engine,
    no_connector_writes: None,
) -> None:
    out = _run(container, ctx_for, scripts.good(), discrepancy["ticket_id"])
    assert out.final_state is RunState.PENDING_REVIEW, out.reasons
    assert out.state_path == (
        RunState.RECEIVED,
        RunState.CLASSIFIED,
        RunState.RESOLVED,
        RunState.EVIDENCE_GATHERED,
        RunState.DRAFTED,
        RunState.PENDING_REVIEW,
    )
    assert out.finding_status == discrepancy["expected_finding"] == "DISCREPANCY"
    assert out.routed_queue == discrepancy["expected_queue"] == "PAYROLL_OPERATIONS"
    assert out.draft_status == "PENDING_REVIEW" and out.proposal_id and out.trace_written

    # The finding is correct: the stored draft states exactly the server's own calculation.
    finding = container.contributions.get_finding(
        ctx_for("op.alex"), "PLAN-001", "PART-0002", "PP-2026-03"
    )
    ee = next(c for c in finding.components if c.component is ContributionType.EMPLOYEE_DEFERRAL)
    assert not ee.variance.is_zero()  # the seeded one-cent shortfall
    with owner_engine.connect() as c:
        body, created_by, status = c.execute(
            text(
                "SELECT v.body, d.created_by, d.status FROM app.drafts d JOIN app.draft_versions v "
                "USING (draft_id) WHERE d.draft_id = :d"
            ),
            {"d": out.draft_id},
        ).one()
        proposal = c.execute(
            text("SELECT target_queue, status FROM app.action_proposals WHERE proposal_id = :p"),
            {"p": out.proposal_id},
        ).one()
    assert format_money(ee.reported) in body and format_money(ee.expected) in body
    assert (created_by, status) == ("agent.worker", "PENDING_REVIEW")
    assert tuple(proposal) == ("PAYROLL_OPERATIONS", "PROPOSED")  # a proposal, not an assignment

    stored = container.traces.get(discrepancy["tenant_id"], out.run_id)
    assert stored is not None
    h = stored.header
    skill = load_skill("service-ticket-triage")
    assert (h["final_state"], h["provider"], h["skill_version"]) == (
        "PENDING_REVIEW",
        "scripted",
        "service-ticket-triage@1.1.0",
    )
    assert h["skill_sha256"] == skill.sha256 and h["draft_id"] == out.draft_id
    with owner_engine.connect() as c:  # the draft records the same skill version
        assert (
            c.execute(
                text("SELECT skill_version FROM app.draft_versions WHERE draft_id = :d"),
                {"d": out.draft_id},
            ).scalar_one()
            == skill.skill_version
        )
    tools = [t["tool_name"] for t in stored.tool_calls]
    assert tools[:5] == [
        "get_plan_details",
        "get_participant_enrollment",
        "get_applicable_plan_rule",
        "get_applicable_plan_rule",
        "get_payroll_contributions",
    ]
    assert {"create_reply_draft", "propose_ticket_assignment"} <= set(tools)
    assert all(t["decision"] == "allow" for t in stored.tool_calls)
    kinds = {e["record_type"] for e in stored.evidence}
    assert {"plan", "enrollment", "plan_rule", "payroll_row", "pay_period"} <= kinds


def test_matching_contribution_also_reaches_pending_review(
    container: Container, ctx_for: Callable[[str], RequestContext], dataset: Dataset
) -> None:
    lab = _label(dataset, participant_ref="PART-0001", expected_finding="MATCH", flags=[])
    out = _run(container, ctx_for, scripts.good(), lab["ticket_id"])
    assert (out.final_state, out.finding_status) == (RunState.PENDING_REVIEW, "MATCH"), out.reasons


# ---------------------------------------------------------------- acceptance 2 and 3
@pytest.mark.parametrize("name", sorted(scripts.ADVERSARIAL))
def test_adversarial_runs_end_in_manual_review_without_writes(
    container: Container,
    ctx_for: Callable[[str], RequestContext],
    discrepancy: dict[str, Any],
    owner_engine: Engine,
    no_connector_writes: None,
    name: str,
) -> None:
    out = _run(container, ctx_for, scripts.ADVERSARIAL[name](), discrepancy["ticket_id"])
    assert out.final_state is RunState.NEEDS_MANUAL_REVIEW, name
    assert out.draft_status != "PENDING_REVIEW"
    assert out.trace_written
    expected_reason = {
        "invented-amount": "VALIDATOR: ",
        "cross-tenant-call": "HOST_REFUSED_TOOL_CALL: host_refused_reference_outside_resolution",
        "unlisted-tool": "HOST_REFUSED_TOOL_CALL: host_refused_unlisted_tool",
        "close-ticket": "HOST_REFUSED_TOOL_CALL: host_refused_unlisted_tool",
        "malformed-json": "MODEL_PROVIDEROUTPUTERROR",
        "timeout": "MODEL_PROVIDERTIMEOUT",
    }[name]
    assert any(r.startswith(expected_reason) for r in out.reasons), out.reasons
    if name == "invented-amount":  # the server validator caught it; the draft waits for a human
        with owner_engine.connect() as c:
            row = c.execute(
                text("SELECT status, status_reasons::text FROM app.drafts WHERE draft_id = :d"),
                {"d": out.draft_id},
            ).one()
        assert row[0] == "NEEDS_MANUAL_REVIEW" and "UNSUPPORTED_AMOUNT" in row[1]
        assert "123.45" not in row[1]
    else:
        assert out.draft_id is None  # no draft at all


def test_refused_tool_calls_are_traced_as_denials(
    container: Container, ctx_for: Callable[[str], RequestContext], discrepancy: dict[str, Any]
) -> None:
    out = _run(container, ctx_for, scripts.close_ticket_request(), discrepancy["ticket_id"])
    stored = container.traces.get(discrepancy["tenant_id"], out.run_id)
    assert stored is not None
    denied = [t for t in stored.tool_calls if t["decision"] == "deny"]
    assert denied and denied[0]["tool_name"] == "close_ticket"
    assert denied[0]["outcome"] == "host_refused_unlisted_tool"


def test_host_runs_each_batch_in_the_skill_order(
    container: Container, ctx_for: Callable[[str], RequestContext], discrepancy: dict[str, Any]
) -> None:
    """The model lists the calls backwards; the host executes them in the documented order."""

    def backwards(req: Any) -> ToolDecision:
        return ToolDecision(
            tool_calls=list(reversed(scripts.evidence_calls(req).tool_calls)), done=True
        )

    script = Script("backwards", scripts.CONFIDENT, [backwards], scripts.faithful_draft)
    out = _run(container, ctx_for, script, discrepancy["ticket_id"])
    assert out.final_state is RunState.PENDING_REVIEW, out.reasons
    stored = container.traces.get(discrepancy["tenant_id"], out.run_id)
    assert stored is not None
    assert [t["tool_name"] for t in stored.tool_calls][:5] == [
        "get_plan_details",
        "get_participant_enrollment",
        "get_applicable_plan_rule",
        "get_applicable_plan_rule",
        "get_payroll_contributions",
    ]


def test_stopping_before_the_evidence_is_complete_fails_closed(
    container: Container, ctx_for: Callable[[str], RequestContext], discrepancy: dict[str, Any]
) -> None:
    script = Script("lazy", scripts.CONFIDENT, [ToolDecision(done=True)], scripts.faithful_draft)
    out = _run(container, ctx_for, script, discrepancy["ticket_id"])
    assert out.final_state is RunState.NEEDS_MANUAL_REVIEW
    assert any(r.startswith("EVIDENCE_NOT_RETRIEVED") for r in out.reasons)


# ---------------------------------------------------------------- routing and findings
def test_injection_ticket_goes_to_human_triage_without_a_draft(
    container: Container,
    ctx_for: Callable[[str], RequestContext],
    dataset: Dataset,
    no_connector_writes: None,
) -> None:
    lab = next(lab for lab in dataset.ticket_labels if "PROMPT_INJECTION" in lab["flags"])
    out = _run(container, ctx_for, scripts.good(), lab["ticket_id"])
    assert out.final_state is RunState.NEEDS_MANUAL_REVIEW
    assert out.state_path == (RunState.RECEIVED, RunState.CLASSIFIED, RunState.NEEDS_MANUAL_REVIEW)
    assert out.routed_queue == "HUMAN_TRIAGE" and out.draft_id is None and out.proposal_id
    assert any("PROMPT_INJECTION" in r for r in out.reasons)


def test_blocking_finding_ends_in_manual_review(
    container: Container, ctx_for: Callable[[str], RequestContext], dataset: Dataset
) -> None:
    lab = _label(dataset, expected_finding="MISSING_PAYROLL_ROW", flags=[])
    out = _run(container, ctx_for, scripts.good(), lab["ticket_id"])
    assert out.final_state is RunState.NEEDS_MANUAL_REVIEW
    assert out.finding_status == "MISSING_PAYROLL_ROW" and out.draft_id is None
    assert any(r.startswith("FINDING_BLOCKS_DRAFT") for r in out.reasons)


def test_every_seeded_contribution_ticket_reaches_its_labelled_outcome(
    container: Container, ctx_for: Callable[[str], RequestContext], dataset: Dataset
) -> None:
    """Host resolution + routing + validator agree with the seed's ground truth on every
    contribution ticket the operators can see (scripted model, so this tests the host only)."""
    operators = {"TEN-ALDER": "op.alex", "TEN-BIRCH": "op.alex", "TEN-COBALT": "op.casey"}
    status = {t["ticket_id"]: t["status"] for t in dataset.tables["zendesk_mock.tickets"]}
    checked = 0
    for lab in dataset.ticket_labels:
        if lab["intent"] != "CONTRIBUTION_DISCREPANCY":
            continue
        out = _run(
            container, ctx_for, scripts.good(), lab["ticket_id"], operators[lab["tenant_id"]]
        )
        checked += 1
        if status[lab["ticket_id"]] in {"solved", "closed"}:
            # Solved/closed tickets never get drafts or proposals, whatever the label says.
            assert out.final_state is RunState.NEEDS_MANUAL_REVIEW
            assert out.reasons == ("TICKET_IS_SOLVED_OR_CLOSED",) and out.proposal_id is None
            continue
        want = (
            RunState.NEEDS_MANUAL_REVIEW
            if lab["requires_manual_review"]
            else RunState.PENDING_REVIEW
        )
        assert out.final_state is want, (lab["ticket_id"], out.reasons)
        assert out.routed_queue == lab["expected_queue"], lab["ticket_id"]
        if lab["expected_finding"] and not set(lab["flags"]):
            assert out.finding_status == lab["expected_finding"], lab["ticket_id"]
    assert checked >= 25


# ---------------------------------------------------------------- budgets, kill switch, authority
def test_wall_clock_budget_fails_closed(
    container: Container, ctx_for: Callable[[str], RequestContext], discrepancy: dict[str, Any]
) -> None:
    ticks = iter(range(0, 10_000, 100))
    out = Orchestrator(
        container,
        ScriptedProvider(scripts.good()),
        budgets=RunBudgets(max_wall_seconds=250),
        monotonic=lambda: float(next(ticks)),
    ).run(ctx_for("op.alex"), discrepancy["ticket_id"])
    assert out.final_state is RunState.NEEDS_MANUAL_REVIEW and "BUDGET_WALL_CLOCK" in out.reasons


def test_model_call_budget_fails_closed(
    container: Container, ctx_for: Callable[[str], RequestContext], discrepancy: dict[str, Any]
) -> None:
    out = _run(
        container,
        ctx_for,
        scripts.good(),
        discrepancy["ticket_id"],
        budgets=RunBudgets(max_model_calls=2),
    )
    assert out.final_state is RunState.NEEDS_MANUAL_REVIEW and "BUDGET_MODEL_CALLS" in out.reasons


def test_kill_switch_blocks_new_runs(
    container: Container,
    ctx_for: Callable[[str], RequestContext],
    discrepancy: dict[str, Any],
    no_connector_writes: None,
) -> None:
    provider = ScriptedProvider(scripts.good())
    out = Orchestrator(container, provider, kill_switch=True).run(
        ctx_for("op.alex"), discrepancy["ticket_id"]
    )
    assert out.final_state is RunState.NEEDS_MANUAL_REVIEW and out.reasons[0] == "KILL_SWITCH_ON"
    assert provider.requests == [] and out.proposal_id is None  # the model was never called


def test_operator_must_be_able_to_see_the_ticket(
    container: Container, ctx_for: Callable[[str], RequestContext], dataset: Dataset
) -> None:
    cobalt = next(
        t["ticket_id"]
        for t in dataset.tables["zendesk_mock.tickets"]
        if t["tenant_id"] == "TEN-COBALT"
    )
    with pytest.raises(NotFound):
        _run(container, ctx_for, scripts.good(), cobalt, "op.alex")  # op.alex has no Cobalt access


def test_run_outcome_is_audited_without_raw_identifiers(
    container: Container,
    ctx_for: Callable[[str], RequestContext],
    discrepancy: dict[str, Any],
    owner_engine: Engine,
) -> None:
    out = _run(container, ctx_for, scripts.good(), discrepancy["ticket_id"])
    with owner_engine.connect() as c:
        details = c.execute(
            text(
                "SELECT details::text FROM app.audit_events WHERE event_type = 'agent_run.finished' "
                "AND target_ref = :r"
            ),
            {"r": out.run_id},
        ).scalar_one()
    assert "PENDING_REVIEW" in details and "PART-" not in details


def test_host_reports_missing_evidence_and_the_model_can_complete_it(
    container: Container, ctx_for: Callable[[str], RequestContext], discrepancy: dict[str, Any]
) -> None:
    """Mirrors the live qwen3:8b behaviour: one rule type forgotten, then fetched after feedback."""
    import json

    def partial(req: Any) -> ToolDecision:
        calls = [
            c
            for c in scripts.evidence_calls(req).tool_calls
            if c.arguments.get("contribution_type") != "EMPLOYEE_DEFERRAL"
        ]
        return ToolDecision(tool_calls=calls, done=True)

    def follow_feedback(req: Any) -> ToolDecision:
        last = req.results[-1]
        assert last.name == "host_evidence_check" and not last.ok
        missing = json.loads(last.content)["missing_evidence"]
        assert [m["record_type"] for m in missing] == ["plan_rule"]
        call = missing[0]["call"]
        assert call["arguments"]["contribution_type"] == "EMPLOYEE_DEFERRAL"
        return ToolDecision(tool_calls=[ToolCall(**call)], done=True)

    script = Script(
        "feedback", scripts.CONFIDENT, [partial, follow_feedback], scripts.faithful_draft
    )
    out = _run(container, ctx_for, script, discrepancy["ticket_id"])
    assert out.final_state is RunState.PENDING_REVIEW, out.reasons


def test_repeated_identical_calls_are_not_executed_twice(
    container: Container, ctx_for: Callable[[str], RequestContext], discrepancy: dict[str, Any]
) -> None:
    def first(req: Any) -> ToolDecision:  # three calls, each listed twice (6 = the turn limit)
        calls = scripts.evidence_calls(req).tool_calls[:3]
        return ToolDecision(tool_calls=[*calls, *calls], done=False)

    def rest(req: Any) -> ToolDecision:  # the other two, plus one repeat from the first turn
        calls = scripts.evidence_calls(req).tool_calls
        return ToolDecision(tool_calls=[*calls[3:], calls[0]], done=True)

    script = Script("twice", scripts.CONFIDENT, [first, rest], scripts.faithful_draft)
    out = _run(container, ctx_for, script, discrepancy["ticket_id"])
    assert out.final_state is RunState.PENDING_REVIEW
    stored = container.traces.get(discrepancy["tenant_id"], out.run_id)
    assert stored is not None
    reads = [t for t in stored.tool_calls if t["tool_name"].startswith("get_")]
    assert len(reads) == 5  # each distinct call once
