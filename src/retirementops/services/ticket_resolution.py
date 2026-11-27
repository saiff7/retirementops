"""Host-side resolution of who a ticket is about (spec §4 step 5; threat model T-03, T-04).

Trusted code, never the model, decides the tenant, participant, plan and pay period of a run:
- the tenant is the ticket's own (from Zendesk), and every lookup is scoped to it alone;
- participant refs (`PART-nnnn`) and employer employee refs (`E-nnnnn`) in the text are looked up
  in that tenant; a ref that is not there is flagged CROSS_TENANT_REFERENCE; an employee ref that
  matches several participants, or several participants mentioned, is AMBIGUOUS_PARTICIPANT;
- the plan comes from a plan ID or plan name in the text, else from the participant's enrollments;
- the pay period comes from a `PP-yyyy-nn` ID or a period END date in the text;
- deterministic detectors add injection, closure, unsupported-action and restricted-pattern flags,
  and an identical earlier ticket in the same tenant adds POSSIBLE_DUPLICATE.
Problems are value-free strings, safe to store and audit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta

from retirementops.connectors.interfaces import PayrollReader, PlanAdminReader, TicketReader
from retirementops.domain.authz import TenantScope
from retirementops.domain.records import PayPeriod, Ticket
from retirementops.domain.risk_detectors import detect_risk_flags
from retirementops.domain.routing import RiskFlag
from retirementops.governance.pseudonym import contains_restricted

_PART = re.compile(r"PART-\d{4}")
_EMP = re.compile(r"(?<![A-Za-z0-9])E-\d{5}")
_PLAN = re.compile(r"PLAN-\d{3}")
_PERIOD = re.compile(r"PP-\d{4}-\d{2}")
_DATE = re.compile(r"(?<![\w-])\d{4}-\d{2}-\d{2}(?![\w-])")
DUPLICATE_WINDOW = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class Resolution:
    ticket_id: str
    tenant_id: str
    participant_refs: tuple[str, ...]
    plan_ids: tuple[str, ...]
    pay_periods: tuple[PayPeriod, ...]
    flags: frozenset[RiskFlag]
    problems: tuple[str, ...]

    @property
    def unique(self) -> bool:
        """Exactly one participant, plan and pay period, with nothing unresolved."""
        return (
            not self.problems
            and len(self.participant_refs) == 1
            and len(self.plan_ids) == 1
            and len(self.pay_periods) == 1
        )


class TicketResolutionService:
    def __init__(
        self, tickets: TicketReader, planadmin: PlanAdminReader, payroll: PayrollReader
    ) -> None:
        self._tickets, self._planadmin, self._payroll = tickets, planadmin, payroll

    def resolve(self, ticket: Ticket) -> Resolution:
        """`ticket` must already have been read under the caller's authority."""
        scope = TenantScope(frozenset({ticket.tenant_id}))  # the ticket's tenant only
        text = f"{ticket.subject}\n{ticket.body}"
        flags: set[RiskFlag] = set(detect_risk_flags(ticket.subject, ticket.body))
        problems: list[str] = []
        if contains_restricted(text):
            flags.add(RiskFlag.CONTAINS_RESTRICTED_PATTERN)

        participants: set[str] = set()
        for ref in sorted(set(_PART.findall(text))):
            if self._planadmin.get_participant(scope, ref) is None:
                flags.add(RiskFlag.CROSS_TENANT_REFERENCE)
                problems.append("a participant reference is not in this ticket's organisation")
            else:
                participants.add(ref)
        for emp in sorted(set(_EMP.findall(text))):
            found = self._planadmin.find_participants_by_employee_ref(scope, emp)
            if len(found) != 1:
                flags.add(RiskFlag.AMBIGUOUS_PARTICIPANT)
                problems.append("an employee reference matches no single participant")
            else:
                participants.add(found[0].participant_ref)
        if len(participants) > 1:
            flags.add(RiskFlag.AMBIGUOUS_PARTICIPANT)
            problems.append("the ticket mentions more than one participant")
        if not participants:
            problems.append("no participant could be identified")

        tenant_plans = self._planadmin.list_plans(scope)
        by_id = {p.plan_id: p for p in tenant_plans}
        plans: set[str] = set()
        for pid in set(_PLAN.findall(text)):
            if pid in by_id:
                plans.add(pid)
            else:
                flags.add(RiskFlag.CROSS_TENANT_REFERENCE)
                problems.append("a plan reference is not in this ticket's organisation")
        lowered = text.lower()
        plans |= {p.plan_id for p in tenant_plans if p.name.lower() in lowered}
        if not plans and len(participants) == 1:
            ref = next(iter(participants))
            plans = {
                p.plan_id
                for p in tenant_plans
                if self._planadmin.list_enrollments(scope, ref, p.plan_id)
            }
        if len(plans) != 1:
            problems.append("the plan could not be identified uniquely")

        calendar = self._payroll.list_pay_periods()
        mentioned_ids = set(_PERIOD.findall(text))
        mentioned_dates = set(_DATE.findall(text))
        periods = [
            p
            for p in calendar
            if p.pay_period_id in mentioned_ids or p.period_end.isoformat() in mentioned_dates
        ]
        if len(mentioned_ids - {p.pay_period_id for p in calendar}):
            problems.append("a pay period reference does not exist")
        if len(periods) != 1:
            problems.append("the pay period could not be identified uniquely")

        if self._has_earlier_duplicate(scope, ticket):
            flags.add(RiskFlag.POSSIBLE_DUPLICATE)

        return Resolution(
            ticket_id=ticket.ticket_id,
            tenant_id=ticket.tenant_id,
            participant_refs=tuple(sorted(participants)),
            plan_ids=tuple(sorted(plans)),
            pay_periods=tuple(sorted(periods, key=lambda p: p.pay_period_id)),
            flags=frozenset(flags),
            problems=tuple(dict.fromkeys(problems)),
        )

    def _has_earlier_duplicate(self, scope: TenantScope, ticket: Ticket) -> bool:
        similar = self._tickets.search_tickets(
            scope, query=ticket.subject[:100], status=None, priority=None, limit=25
        )
        return any(
            t.ticket_id != ticket.ticket_id
            and t.subject == ticket.subject
            and t.body == ticket.body
            and t.created_at < ticket.created_at
            and ticket.created_at - t.created_at <= DUPLICATE_WINDOW
            for t in similar
        )
