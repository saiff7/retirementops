"""Tool input models (the contract) and output builders for the Service Operations MCP server.

Every input model forbids extra fields, bounds string lengths and page sizes, and uses strict integers.
The JSON Schema advertised in `tools/list` is generated from these models with
`additionalProperties: false`. The server validates arguments itself and never relies on the client.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from retirementops.domain.evidence import SourceRef
from retirementops.domain.money import Money, format_money, format_percent
from retirementops.domain.records import (
    ContributionType,
    Enrollment,
    PayPeriod,
    PayrollRow,
    Plan,
    PlanRule,
    PolicyCategory,
    PolicySnippet,
    Ticket,
)
from retirementops.domain.rules import RuleSelection
from retirementops.domain.taxonomy import Queue, TicketStatus
from retirementops.governance.pseudonym import contains_restricted, mask_restricted

TicketId = Annotated[str, Field(pattern=r"^ZD-\d{4,6}$", description="Ticket id, e.g. ZD-1001")]
PlanId = Annotated[str, Field(pattern=r"^PLAN-\d{3}$", description="Plan id, e.g. PLAN-001")]
ParticipantRef = Annotated[
    str, Field(pattern=r"^PART-\d{4}$", description="Synthetic participant alias, e.g. PART-0001")
]
PayPeriodId = Annotated[str, Field(pattern=r"^PP-\d{4}-\d{2}$", description="e.g. PP-2026-03")]
SearchText = Annotated[str, Field(min_length=1, max_length=200)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SearchTicketsIn(_In):
    query: SearchText | None = Field(None, description="Case-insensitive match on the subject")
    status: TicketStatus | None = None
    priority: Literal["low", "normal", "high", "urgent"] | None = None
    page_size: StrictInt = Field(10, ge=1, le=25)


class GetTicketDetailsIn(_In):
    ticket_id: TicketId


class GetPlanDetailsIn(_In):
    plan_id: PlanId


class GetParticipantEnrollmentIn(_In):
    participant_ref: ParticipantRef
    plan_id: PlanId


class GetPayrollContributionsIn(_In):
    participant_ref: ParticipantRef
    plan_id: PlanId
    pay_period: PayPeriodId


class GetApplicablePlanRuleIn(_In):
    plan_id: PlanId
    effective_date: date
    contribution_type: ContributionType


class GetPolicySnippetsIn(_In):
    query: SearchText | None = None
    category: PolicyCategory | None = None
    limit: StrictInt = Field(5, ge=1, le=10)


class EvidenceRefIn(_In):
    system: Annotated[str, Field(pattern=r"^[a-z_]{1,32}$")]
    record_type: Annotated[str, Field(pattern=r"^[a-z_]{1,32}$")]
    record_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9#-]{1,64}$")]
    content_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class CreateReplyDraftIn(_In):
    ticket_id: TicketId
    body: Annotated[str, Field(min_length=1, max_length=10_000)]
    evidence_refs: Annotated[
        list[EvidenceRefIn],
        Field(min_length=1, max_length=50, description="Must include the ticket being answered"),
    ]
    skill_version: Annotated[
        str,
        Field(
            pattern=r"^[a-z0-9-]{1,64}@\d+\.\d+\.\d+$",
            description="e.g. service-ticket-triage@1.0.0",
        ),
    ]

    idempotency_key: Annotated[
        str | None,
        Field(
            pattern=r"^[A-Za-z0-9:_-]{8,128}$",
            description="Optional. A retry with the same key and content returns the original draft.",
        ),
    ] = None


class ProposeTicketAssignmentIn(_In):
    ticket_id: TicketId
    target_queue: Queue
    rationale: Annotated[str, Field(min_length=1, max_length=1_000)]


class GetTicketActionStatusIn(_In):
    proposal_id: Annotated[str, Field(pattern=r"^PRP-[0-9A-F]{16}$")]


def input_schema(model: type[_In]) -> dict[str, Any]:
    schema = model.model_json_schema()
    schema["additionalProperties"] = False  # explicit, even though extra="forbid" implies it
    return schema


# ---------------------------------------------------------------- outputs (JSON-safe dicts)
def source(s: SourceRef) -> dict[str, str]:
    return {
        "system": s.system,
        "record_type": s.record_type,
        "record_id": s.record_id,
        "record_version": s.record_version,
        "retrieved_at": s.retrieved_at.isoformat(),
        "content_hash": s.content_hash,
    }


def money(m: Money) -> dict[str, str]:
    return {"amount": str(m.amount), "currency": m.currency, "display": format_money(m)}


def ticket_summary(t: Ticket) -> dict[str, Any]:
    return {
        "ticket_id": t.ticket_id,
        # Review Low-9: the subject is requester text too, so it carries the same trust label.
        "subject": {"trust": "UNTRUSTED_REQUESTER_TEXT", "text": mask_restricted(t.subject)},
        "status": t.status.value,
        "priority": t.priority,
        "assignee_queue": t.assignee_queue.value if t.assignee_queue else None,
        "updated_at": t.updated_at.isoformat(),
        "version": t.version,
        "source": source(t.source),
    }


def ticket_details(t: Ticket) -> dict[str, Any]:
    restricted = contains_restricted(t.body) or contains_restricted(t.subject)
    return {
        **ticket_summary(t),
        "channel": t.channel,
        "created_at": t.created_at.isoformat(),
        # Requester text is DATA, never instructions. Restricted patterns are masked before any
        # model sees them (data-classification.md, control 6).
        "body": {
            "trust": "UNTRUSTED_REQUESTER_TEXT",
            "text": mask_restricted(t.body),
            "contains_restricted_pattern": restricted,
        },
    }


def plan(p: Plan) -> dict[str, Any]:
    return {
        "plan_id": p.plan_id,
        "name": p.name,
        "currency": p.currency,
        "source": source(p.source),
    }


def rule(r: PlanRule) -> dict[str, Any]:
    return {
        "rule_id": r.rule_id,
        "contribution_type": r.contribution_type.value,
        "rule_version": r.rule_version,
        "formula_code": r.formula_code,
        "max_deferral_pct": str(r.max_deferral_pct) if r.max_deferral_pct is not None else None,
        "match_rate": str(r.match_rate) if r.match_rate is not None else None,
        "match_cap_pct": str(r.match_cap_pct) if r.match_cap_pct is not None else None,
        "rounding": r.rounding,
        "effective_from": r.effective_from.isoformat(),
        "effective_to": r.effective_to.isoformat() if r.effective_to else None,
        "fictional": True,
        "source": source(r.source),
    }


def rule_selection(sel: RuleSelection) -> dict[str, Any]:
    return {
        "status": sel.status.value,
        "rule": rule(sel.rule) if sel.rule else None,
        "candidate_rule_ids": list(sel.candidate_ids),
    }


def enrollment(e: Enrollment) -> dict[str, Any]:
    return {
        "enrollment_id": e.enrollment_id,
        "participant_ref": e.participant_ref,
        "plan_id": e.plan_id,
        "status": e.status.value,
        "deferral_pct": str(e.deferral_pct),
        "deferral_display": format_percent(e.deferral_pct),
        "effective_from": e.effective_from.isoformat(),
        "effective_to": e.effective_to.isoformat() if e.effective_to else None,
        "source": source(e.source),
    }


def pay_period(p: PayPeriod) -> dict[str, Any]:
    return {
        "pay_period_id": p.pay_period_id,
        "period_start": p.period_start.isoformat(),
        "period_end": p.period_end.isoformat(),
        "pay_date": p.pay_date.isoformat(),
        "source": source(p.source),
    }


def payroll_row(r: PayrollRow) -> dict[str, Any]:
    return {
        "file_id": r.file_id,
        "eligible_pay": money(r.eligible_pay),
        "employee_contribution": money(r.employee_contribution),
        "employer_contribution": money(r.employer_contribution),
        "source": source(r.source),
    }


def policy(s: PolicySnippet) -> dict[str, Any]:
    return {
        "snippet_id": s.snippet_id,
        "category": s.category.value,
        "title": s.title,
        "text": s.body,
        "version": s.version,
        "effective_from": s.effective_from.isoformat(),
        "effective_to": s.effective_to.isoformat() if s.effective_to else None,
        "fictional": True,
        "source": source(s.source),
    }
