"""Canned scripts for ScriptedProvider: one well-behaved run and the M3 adversarial runs.

Adversarial scripts (acceptance: each must end in NEEDS_MANUAL_REVIEW, no connector write):
invented amount, cross-tenant call, unlisted tool, malformed JSON, timeout, and a request for a
close tool. The well-behaved script uses only host-resolved references and host facts.
"""

from __future__ import annotations

from retirementops.agent_runtime.providers.contracts import (
    ClassificationResult,
    DraftRequest,
    DraftResponse,
    ProviderTimeout,
    ToolCall,
    ToolDecision,
    ToolDecisionRequest,
)
from retirementops.agent_runtime.providers.scripted import Script
from retirementops.domain.taxonomy import Intent

CONFIDENT = ClassificationResult(
    intent=Intent.CONTRIBUTION_DISCREPANCY,
    confidence=0.93,
    summary="Sponsor reports a contribution that may not match the payroll file.",
)


def evidence_calls(req: ToolDecisionRequest) -> ToolDecision:
    """The contribution-investigation order, using only the resolved references."""
    ref, plan = req.resolved.participant_refs[0], req.resolved.plan_ids[0]
    period, end = req.resolved.pay_period_ids[0], req.resolved.pay_period_ends[0]
    calls = [
        ToolCall(name="get_plan_details", arguments={"plan_id": plan}),
        ToolCall(
            name="get_participant_enrollment",
            arguments={"participant_ref": ref, "plan_id": plan},
        ),
        ToolCall(
            name="get_applicable_plan_rule",
            arguments={
                "plan_id": plan,
                "effective_date": end,
                "contribution_type": "EMPLOYEE_DEFERRAL",
            },
        ),
        ToolCall(
            name="get_applicable_plan_rule",
            arguments={
                "plan_id": plan,
                "effective_date": end,
                "contribution_type": "EMPLOYER_MATCH",
            },
        ),
        ToolCall(
            name="get_payroll_contributions",
            arguments={"participant_ref": ref, "plan_id": plan, "pay_period": period},
        ),
    ]
    return ToolDecision(tool_calls=calls, done=False)


def faithful_draft(req: DraftRequest) -> DraftResponse:
    f = req.facts
    if f.get("finding_status") == "MATCH":
        middle = (
            f"the payroll file we received reports an employee contribution of "
            f"{f['employee_reported']} and an employer match of {f['employer_reported']}, which "
            "equal the amounts expected under the plan's fictional rules."
        )
    else:
        middle = (
            f"the payroll file we received reports an employee contribution of "
            f"{f['employee_reported']}; the amount expected under the plan's fictional rule is "
            f"{f['employee_expected']}. The employer match reported is {f['employer_reported']}; "
            f"the expected amount is {f['employer_expected']}."
        )
    body = (
        f"Thank you for raising this. For participant {f['participant_ref']} in plan "
        f"{f['plan_id']}, pay period {f['pay_period_id']} (ending {f['period_end']}), {middle} "
        "Our payroll operations team will review this finding with you."
    )
    return DraftResponse(body=body, needs_human=False)


def good() -> Script:
    return Script("good", CONFIDENT, [evidence_calls], faithful_draft)


def invented_amount() -> Script:
    def draft(req: DraftRequest) -> DraftResponse:
        body = faithful_draft(req).body.replace(req.facts["employee_expected"], "$123.45")
        return DraftResponse(body=body, needs_human=False)

    return Script("invented-amount", CONFIDENT, [evidence_calls], draft)


def cross_tenant_call() -> Script:
    turn = ToolDecision(
        tool_calls=[
            ToolCall(
                name="get_participant_enrollment",
                arguments={"participant_ref": "PART-0030", "plan_id": "PLAN-003"},
            )
        ],
        done=False,
    )
    return Script("cross-tenant-call", CONFIDENT, [turn], faithful_draft)


def unlisted_tool() -> Script:
    turn = ToolDecision(
        tool_calls=[ToolCall(name="search_tickets", arguments={"query": "contribution"})],
        done=False,
    )
    return Script("unlisted-tool", CONFIDENT, [turn], faithful_draft)


def close_ticket_request() -> Script:
    turn = ToolDecision(
        tool_calls=[ToolCall(name="close_ticket", arguments={"ticket_id": "ZD-1001"})],
        done=False,
    )
    return Script("close-ticket", CONFIDENT, [turn], faithful_draft)


def malformed_json() -> Script:
    return Script(
        "malformed-json", '{"intent": "CONTRIBUTION_DISCREPANCY", "confidence": ', [], None
    )


def timeout() -> Script:
    return Script("timeout", CONFIDENT, [ProviderTimeout("scripted timeout")], faithful_draft)


ADVERSARIAL = {
    "invented-amount": invented_amount,
    "cross-tenant-call": cross_tenant_call,
    "unlisted-tool": unlisted_tool,
    "close-ticket": close_ticket_request,
    "malformed-json": malformed_json,
    "timeout": timeout,
}
