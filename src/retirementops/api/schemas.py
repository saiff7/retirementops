"""Response/request models. Explicit field allowlists: nothing is serialised that isn't listed here."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from retirementops.domain.contributions import ContributionFinding
from retirementops.domain.evidence import SourceRef
from retirementops.domain.money import Money, format_money, format_percent
from retirementops.domain.records import Enrollment, PayPeriod, PayrollRow, Plan, PlanRule, Ticket
from retirementops.domain.rules import RuleSelection


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceOut(_Out):
    system: str
    record_type: str
    record_id: str
    record_version: str
    retrieved_at: datetime
    content_hash: str

    @classmethod
    def of(cls, s: SourceRef) -> SourceOut:
        return cls(
            system=s.system,
            record_type=s.record_type,
            record_id=s.record_id,
            record_version=s.record_version,
            retrieved_at=s.retrieved_at,
            content_hash=s.content_hash,
        )


class MoneyOut(_Out):
    amount: str  # decimal string, never a float
    currency: str
    display: str

    @classmethod
    def of(cls, m: Money) -> MoneyOut:
        return cls(amount=str(m.amount), currency=m.currency, display=format_money(m))


class TicketSummaryOut(_Out):
    ticket_id: str
    subject: str
    status: str
    priority: str
    assignee_queue: str | None
    created_at: datetime
    updated_at: datetime
    version: int

    @classmethod
    def of(cls, t: Ticket) -> TicketSummaryOut:
        return cls(
            ticket_id=t.ticket_id,
            subject=t.subject,
            status=t.status.value,
            priority=t.priority,
            assignee_queue=t.assignee_queue.value if t.assignee_queue else None,
            created_at=t.created_at,
            updated_at=t.updated_at,
            version=t.version,
        )


class TicketOut(TicketSummaryOut):
    body: str  # untrusted requester text; callers must treat as data
    channel: str
    source: SourceOut

    @classmethod
    def of(cls, t: Ticket) -> TicketOut:
        base = TicketSummaryOut.of(t).model_dump()
        return cls(**base, body=t.body, channel=t.channel, source=SourceOut.of(t.source))


class TicketPage(_Out):
    items: list[TicketSummaryOut]
    page_size: int


class PlanOut(_Out):
    plan_id: str
    name: str
    currency: str
    source: SourceOut

    @classmethod
    def of(cls, p: Plan) -> PlanOut:
        return cls(
            plan_id=p.plan_id, name=p.name, currency=p.currency, source=SourceOut.of(p.source)
        )


class PlanRuleOut(_Out):
    rule_id: str
    contribution_type: str
    rule_version: str
    formula_code: str
    max_deferral_pct: Decimal | None
    match_rate: Decimal | None
    match_cap_pct: Decimal | None
    rounding: str
    effective_from: date
    effective_to: date | None
    fictional: bool = True
    source: SourceOut

    @classmethod
    def of(cls, r: PlanRule) -> PlanRuleOut:
        return cls(
            rule_id=r.rule_id,
            contribution_type=r.contribution_type.value,
            rule_version=r.rule_version,
            formula_code=r.formula_code,
            max_deferral_pct=r.max_deferral_pct,
            match_rate=r.match_rate,
            match_cap_pct=r.match_cap_pct,
            rounding=r.rounding,
            effective_from=r.effective_from,
            effective_to=r.effective_to,
            source=SourceOut.of(r.source),
        )


class RuleSelectionOut(_Out):
    status: str
    rule: PlanRuleOut | None
    candidate_rule_ids: list[str]

    @classmethod
    def of(cls, s: RuleSelection) -> RuleSelectionOut:
        return cls(
            status=s.status.value,
            rule=PlanRuleOut.of(s.rule) if s.rule else None,
            candidate_rule_ids=list(s.candidate_ids),
        )


class EnrollmentOut(_Out):
    enrollment_id: str
    participant_ref: str
    plan_id: str
    status: str
    deferral_pct: Decimal
    deferral_display: str
    effective_from: date
    effective_to: date | None
    source: SourceOut

    @classmethod
    def of(cls, e: Enrollment) -> EnrollmentOut:
        return cls(
            enrollment_id=e.enrollment_id,
            participant_ref=e.participant_ref,
            plan_id=e.plan_id,
            status=e.status.value,
            deferral_pct=e.deferral_pct,
            deferral_display=format_percent(e.deferral_pct),
            effective_from=e.effective_from,
            effective_to=e.effective_to,
            source=SourceOut.of(e.source),
        )


class PayPeriodOut(_Out):
    pay_period_id: str
    period_start: date
    period_end: date
    pay_date: date

    @classmethod
    def of(cls, p: PayPeriod) -> PayPeriodOut:
        return cls(
            pay_period_id=p.pay_period_id,
            period_start=p.period_start,
            period_end=p.period_end,
            pay_date=p.pay_date,
        )


class PayrollRowOut(_Out):
    file_id: str
    eligible_pay: MoneyOut
    employee_contribution: MoneyOut
    employer_contribution: MoneyOut
    source: SourceOut

    @classmethod
    def of(cls, r: PayrollRow) -> PayrollRowOut:
        return cls(
            file_id=r.file_id,
            eligible_pay=MoneyOut.of(r.eligible_pay),
            employee_contribution=MoneyOut.of(r.employee_contribution),
            employer_contribution=MoneyOut.of(r.employer_contribution),
            source=SourceOut.of(r.source),
        )


class ContributionRowsOut(_Out):
    participant_ref: str
    plan_id: str
    pay_period: PayPeriodOut
    rows: list[PayrollRowOut]


class ComponentOut(_Out):
    component: str
    rule_id: str
    rule_version: str
    formula: str
    expected: MoneyOut
    reported: MoneyOut
    variance: MoneyOut


class FindingOut(_Out):
    status: str
    blocking: bool
    participant_ref: str
    plan_id: str
    pay_period_id: str
    determination_date: str
    calc_version: str
    rounding: str
    eligible_pay: MoneyOut | None
    effective_deferral_pct: Decimal | None
    components: list[ComponentOut]
    reasons: list[str]
    evidence: list[SourceOut]
    computed_by: str = "deterministic Python (no model involvement)"

    @classmethod
    def of(cls, f: ContributionFinding) -> FindingOut:
        return cls(
            status=f.status.value,
            blocking=f.is_blocking,
            participant_ref=f.participant_ref,
            plan_id=f.plan_id,
            pay_period_id=f.pay_period_id,
            determination_date=f.determination_date,
            calc_version=f.calc_version,
            rounding=f.rounding,
            eligible_pay=MoneyOut.of(f.eligible_pay) if f.eligible_pay else None,
            effective_deferral_pct=f.effective_deferral_pct,
            components=[
                ComponentOut(
                    component=c.component.value,
                    rule_id=c.rule_id,
                    rule_version=c.rule_version,
                    formula=c.formula,
                    expected=MoneyOut.of(c.expected),
                    reported=MoneyOut.of(c.reported),
                    variance=MoneyOut.of(c.variance),
                )
                for c in f.components
            ],
            reasons=list(f.reasons),
            evidence=[SourceOut.of(s) for s in f.evidence_refs],
        )


class DevTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str


class DevTokenOut(_Out):
    access_token: str
    token_type: str = "Bearer"  # noqa: S105 - a scheme name, not a secret
    warning: str = "Mock identity for local development. Not production SSO."


class ErrorOut(_Out):
    error: str
    message: str
    correlation_id: str
