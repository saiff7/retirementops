"""M4 Stage 4: deterministic scorers, on hand-built results (no database, no model)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from retirementops.domain.money import Money, format_money
from retirementops.evaluations import dataset as gd
from retirementops.evaluations import scorers as S
from retirementops.evaluations.runner import CaseResult, DraftFeatures, ToolCallRecord


@pytest.fixture(scope="module")
def golden() -> gd.GoldenDataset:
    return gd.load()


def _case(golden: gd.GoldenDataset, cid: str) -> gd.GoldenCase:
    return golden.by_id()[cid]


def _perfect(c: gd.GoldenCase, **over: Any) -> CaseResult:
    """A result that meets every expectation of `c` (the faithful path)."""
    e = c.expected
    evidence = [
        {"system": "x", "record_type": x.record_type, "record_id": x.record_id or "PF-1#1"}
        for x in e.required_evidence
    ]
    draft = None
    if e.final_state == "PENDING_REVIEW":
        assert e.amounts
        a = e.amounts
        draft = DraftFeatures(
            status="PENDING_REVIEW",
            validator_version="draft-validator/2",
            created_by="agent.worker",
            amounts=sorted(
                {
                    format_money(Money(Decimal(a.employee_reported))),
                    format_money(Money(Decimal(a.employee_expected))),
                }
            ),
            identifiers=sorted(
                x
                for x in (c.subject.participant_ref, c.subject.plan_id, c.subject.pay_period_id)
                if x
            ),
            dates=[],
            action_claims=0,
            body_sha256="0" * 64,
            cited=[{"system": "zendesk_mock", "record_type": "ticket", "record_id": c.ticket_id}]
            + [
                {"system": "x", "record_type": x["record_type"], "record_id": x["record_id"]}
                for x in evidence
            ],
        )
    first = []
    if e.reason in S.EVIDENCE_PHASE_REASONS:
        s = c.subject
        first = [
            {"name": "get_plan_details", "arguments": {"plan_id": s.plan_id}},
            {
                "name": "get_participant_enrollment",
                "arguments": {"participant_ref": s.participant_ref, "plan_id": s.plan_id},
            },
            {
                "name": "get_applicable_plan_rule",
                "arguments": {"plan_id": s.plan_id, "contribution_type": "EMPLOYEE_DEFERRAL"},
            },
            {
                "name": "get_applicable_plan_rule",
                "arguments": {"plan_id": s.plan_id, "contribution_type": "EMPLOYER_MATCH"},
            },
            {
                "name": "get_payroll_contributions",
                "arguments": {
                    "participant_ref": s.participant_ref,
                    "plan_id": s.plan_id,
                    "pay_period": s.pay_period_id,
                },
            },
        ]
    base: dict[str, Any] = {
        "case_id": c.case_id,
        "split": c.split,
        "category": c.category,
        "repeat": 1,
        "script": "test",
        "final_state": e.final_state,
        "reason_codes": [e.reason] if e.reason else [],
        "predicted_intent": e.intent.value,
        "routed_queue": e.queue.value,
        "finding_status": e.finding.value if e.finding else None,
        "draft": draft,
        "proposals": [{"queue": e.queue.value, "status": "PROPOSED", "by": "agent.worker"}],
        "trace_written": True,
        "trace_found": True,
        "evidence": evidence if e.reason in S.EVIDENCE_PHASE_REASONS else [],
        "evidence_tenants": [c.tenant_id] if evidence else [],
        "cited_tenants": [c.tenant_id] if draft else [],
        "first_pass_calls": first,
        "all_model_calls": first,
        "model_calls": 3,
        "model_latency_ms": [10.0, 20.0, 30.0],
        "run_latency_ms": 100.0,
    }
    base.update(over)
    return CaseResult(**base)


def test_perfect_results_score_perfectly(golden: gd.GoldenDataset) -> None:
    results = [_perfect(c) for c in golden.cases]
    s = S.score(golden.cases, results)
    for key in (
        "intent_accuracy",
        "intent_macro_f1",
        "routing_accuracy",
        "manual_review_recall",
        "manual_review_precision",
        "evidence_coverage",
        "citation_precision",
        "evidence_ownership_accepted_drafts",
        "accepted_outputs_schema_valid",
        "tool_selection_precision",
        "tool_selection_recall",
        "tool_argument_accuracy",
        "injection_resistance",
        "case_pass_rate",
    ):
        assert s[key] == 1.0, key
    assert s["unsupported_claim_rate"] == 0.0 and s["parse_failure_rate"] == 0.0
    assert s["cross_tenant_leaks"] == s["mock_system_writes"] == s["missing_traces"] == 0
    assert s["duplicate_open_proposals"] == 0
    for key in ("review_queue_age", "approval_rate", "rejection_rate"):
        assert s[key] == S.NOT_APPLICABLE_UNTIL_M5


def test_wrong_intent_and_queue_are_counted(golden: gd.GoldenDataset) -> None:
    c = _case(golden, "G-DEV-005")
    r = _perfect(c, predicted_intent="GENERAL_INFORMATION", routed_queue="PLAN_ADMINISTRATION")
    s = S.score([c], [r])
    assert s["intent_accuracy"] == 0.0 and s["routing_accuracy"] == 0.0
    ok, fails = S.case_passed(c, r)
    assert not ok and "QUEUE" in fails


def test_missing_trace_fails_the_case(golden: gd.GoldenDataset) -> None:
    c = _case(golden, "G-DEV-002")
    ok, fails = S.case_passed(c, _perfect(c, trace_found=False))
    assert not ok and fails == ["TRACE_MISSING"]
    assert S.score([c], [_perfect(c, trace_written=False)])["missing_traces"] == 1


def test_invented_amount_is_an_unsupported_claim(golden: gd.GoldenDataset) -> None:
    c = _case(golden, "G-DEV-002")
    good = _perfect(c)
    assert good.draft is not None
    bad = _perfect(
        c, draft=good.draft.model_copy(update={"amounts": [*good.draft.amounts, "$123.45"]})
    )
    assert S.unsupported_claims(c, bad) == ["UNSUPPORTED_AMOUNT"]
    s = S.score([c], [bad])
    assert s["unsupported_claim_rate_accepted_drafts"] == 1.0
    assert "UNSUPPORTED_CLAIM" in S.case_passed(c, bad)[1]


def test_difference_amount_and_other_oracle_values_are_supported(golden: gd.GoldenDataset) -> None:
    c = _case(golden, "G-DEV-002")  # one-cent shortfall: $0.01 is a supported difference
    good = _perfect(c)
    assert good.draft is not None
    r = _perfect(
        c,
        draft=good.draft.model_copy(
            update={"amounts": [*good.draft.amounts, "$0.01", "$4,052.18"]}
        ),
    )
    assert S.unsupported_claims(c, r) == []


def test_action_claim_and_foreign_identifier_are_unsupported(golden: gd.GoldenDataset) -> None:
    c = _case(golden, "G-DEV-002")
    good = _perfect(c)
    assert good.draft is not None
    r = _perfect(
        c,
        draft=good.draft.model_copy(
            update={"action_claims": 1, "identifiers": [*good.draft.identifiers, "PART-0030"]}
        ),
    )
    assert sorted(S.unsupported_claims(c, r)) == ["ACTION_CLAIM", "UNSUPPORTED_IDENTIFIER"]


def test_missing_key_amount_is_flagged(golden: gd.GoldenDataset) -> None:
    c = _case(golden, "G-DEV-002")
    good = _perfect(c)
    assert good.draft is not None
    r = _perfect(c, draft=good.draft.model_copy(update={"amounts": []}))
    assert S.unsupported_claims(c, r) == ["MISSING_KEY_AMOUNT", "MISSING_KEY_AMOUNT"]


def test_foreign_tenant_citation_breaks_ownership(golden: gd.GoldenDataset) -> None:
    c = _case(golden, "G-DEV-002")
    r = _perfect(c, cited_tenants=["TEN-ALDER", "TEN-BIRCH"])
    s = S.score([c], [r])
    assert s["evidence_ownership_accepted_drafts"] == 0.0 and s["cross_tenant_leaks"] == 1
    assert s["unauthorized_attempts_denied"] == 0.0  # one undenied attempt, nothing denied


def test_first_pass_tool_scores(golden: gd.GoldenDataset) -> None:
    c = _case(golden, "G-DEV-002")
    full = _perfect(c)
    partial = _perfect(
        c,
        first_pass_calls=[
            *full.first_pass_calls[:2],
            {"name": "get_plan_details", "arguments": {"plan_id": "PLAN-003"}},
        ],
    )
    t = S.tool_scores_for_case(c, partial)
    assert t is not None
    assert t["recall"] == 0.4 and t["precision"] == 1.0
    assert t["argument_accuracy"] == round(2 / 3, 4)
    none = S.tool_scores_for_case(c, _perfect(c, first_pass_calls=[]))
    assert none == {"precision": 0.0, "recall": 0.0, "argument_accuracy": 0.0, "calls": 0}
    assert S.tool_scores_for_case(_case(golden, "G-DEV-016"), full) is None  # no evidence phase


def test_evidence_coverage_counts_payroll_rows_by_type(golden: gd.GoldenDataset) -> None:
    c = _case(golden, "G-DEV-014")  # two payroll rows for one period
    need = len(c.expected.required_evidence)
    r = _perfect(c)
    assert S.evidence_coverage(c, r) == (need, need)
    fewer = _perfect(c, evidence=[e for e in r.evidence if e["record_type"] != "payroll_row"])
    assert S.evidence_coverage(c, fewer) == (need - 2, need)


def test_manual_review_scores(golden: gd.GoldenDataset) -> None:
    flagged = _case(golden, "G-DEV-016")
    normal = _case(golden, "G-DEV-005")
    missed = _perfect(flagged, final_state="PENDING_REVIEW", reason_codes=[])
    s = S.score([flagged, normal], [missed, _perfect(normal)])
    assert s["manual_review_recall"] == 0.0
    assert S.predicted_review(_perfect(normal)) is False  # out of M3 drafting scope, not review


def test_injection_resistance(golden: gd.GoldenDataset) -> None:
    c = _case(golden, "G-DEV-019")
    assert S.injection_resisted(c, _perfect(c))
    assert not S.injection_resisted(c, _perfect(c, mock_tables_changed=["zendesk_mock.tickets"]))
    assert not S.injection_resisted(c, _perfect(c, evidence_tenants=["TEN-BIRCH"]))


def test_duplicate_open_proposals(golden: gd.GoldenDataset) -> None:
    c = _case(golden, "G-DEV-005")
    p = {"queue": "ENROLLMENT_OPERATIONS", "status": "PROPOSED", "by": "agent.worker"}
    assert S.duplicate_open_proposals(_perfect(c, proposals=[p, p])) == 1
    other = {**p, "queue": "HUMAN_TRIAGE"}
    assert S.duplicate_open_proposals(_perfect(c, proposals=[p, other])) == 0


def test_edge_cases_do_not_divide_by_zero(golden: gd.GoldenDataset) -> None:
    c = _case(golden, "G-DEV-016")
    s = S.score([c], [_perfect(c)])
    assert s["unsupported_claim_rate"] is None  # no drafts at all
    assert s["evidence_ownership_accepted_drafts"] is None
    assert S.score([], [])["cases"] == 0
    assert S.macro_f1([], []) is None
    assert S.percentile([], 50) is None
    assert S.percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.0 and S.percentile([5.0], 95) == 5.0


def test_macro_f1_known_value() -> None:
    truth = ["A", "A", "B", "B"]
    pred = ["A", "B", "B", "B"]
    # A: p=1, r=.5, f1=.6667; B: p=.6667, r=1, f1=.8 -> mean .7333
    assert S.macro_f1(truth, pred) == 0.7333


labels = st.sampled_from(["A", "B", "C", "D"])


@given(st.lists(st.tuples(labels, labels), min_size=1, max_size=40))
def test_macro_f1_properties(pairs: list[tuple[str, str]]) -> None:
    truth, pred = [t for t, _ in pairs], [p for _, p in pairs]
    f1 = S.macro_f1(truth, pred)
    assert f1 is not None and 0.0 <= f1 <= 1.0
    assert S.macro_f1(truth, truth) == 1.0
    if f1 == 1.0:
        assert truth == pred


def test_scores_ignore_the_system_validator_status(golden: gd.GoldenDataset) -> None:
    """Not circular: a draft the system's validator passed still fails on a wrong amount."""
    c = _case(golden, "G-DEV-001")
    good = _perfect(c)
    assert good.draft is not None
    r = _perfect(c, draft=good.draft.model_copy(update={"amounts": ["$1.00"]}))
    assert r.draft is not None and r.draft.status == "PENDING_REVIEW"
    assert "UNSUPPORTED_AMOUNT" in S.unsupported_claims(c, r)


def test_tool_records_type() -> None:
    assert (
        ToolCallRecord(name="x", decision="deny", outcome="host_refused_unlisted_tool").decision
        == "deny"
    )
