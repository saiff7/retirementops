"""Read services for tickets, plans, enrollment and contributions.

Every method: (1) checks scopes on the verified principal, (2) passes the principal's tenant scope to
the connector, which filters explicitly AND runs under Postgres RLS, (3) cross-checks that related
objects (plan, participant) belong to the same tenant. Anything not visible is NotFound.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from retirementops.connectors.interfaces import PayrollReader, PlanAdminReader, TicketReader
from retirementops.domain.authz import Scope, TenantScope
from retirementops.domain.contributions import ContributionFinding, compute_contribution_finding
from retirementops.domain.records import (
    ContributionType,
    Enrollment,
    Participant,
    PayPeriod,
    PayrollRow,
    Plan,
    Ticket,
)
from retirementops.domain.rules import RuleSelection, select_applicable_rule
from retirementops.domain.taxonomy import TicketStatus
from retirementops.services.context import RequestContext
from retirementops.services.errors import InvalidRequest, NotFound

MAX_PAGE_SIZE = 25


class TicketService:
    def __init__(self, reader: TicketReader) -> None:
        self._reader = reader

    def search(
        self,
        ctx: RequestContext,
        *,
        query: str | None,
        status: TicketStatus | None,
        priority: str | None,
        page_size: int,
    ) -> list[Ticket]:
        ctx.require(Scope.TICKETS_READ)
        if not 1 <= page_size <= MAX_PAGE_SIZE:
            raise InvalidRequest(f"page_size must be 1..{MAX_PAGE_SIZE}")
        return self._reader.search_tickets(
            ctx.principal.tenants, query=query, status=status, priority=priority, limit=page_size
        )

    def get(self, ctx: RequestContext, ticket_id: str) -> Ticket:
        ctx.require(Scope.TICKETS_READ)
        ticket = self._reader.get_ticket(ctx.principal.tenants, ticket_id)
        if ticket is None:
            raise NotFound("ticket", ticket_id)
        return ticket


@dataclass(frozen=True, slots=True)
class PlanParticipant:
    plan: Plan
    participant: Participant


class PlanService:
    def __init__(self, planadmin: PlanAdminReader) -> None:
        self._planadmin = planadmin

    def get_plan(self, ctx: RequestContext, plan_id: str) -> Plan:
        ctx.require(Scope.PLANS_READ)
        return self._visible_plan(ctx.principal.tenants, plan_id)

    def _visible_plan(self, scope: TenantScope, plan_id: str) -> Plan:
        plan = self._planadmin.get_plan(scope, plan_id)
        if plan is None:
            raise NotFound("plan", plan_id)
        return plan

    def resolve_plan_participant(
        self, ctx: RequestContext, plan_id: str, participant_ref: str
    ) -> PlanParticipant:
        """Both must be visible AND belong to the same tenant (object-level check)."""
        scope = ctx.principal.tenants
        plan = self._visible_plan(scope, plan_id)
        participant = self._planadmin.get_participant(scope, participant_ref)
        if participant is None or participant.tenant_id != plan.tenant_id:
            raise NotFound("participant", participant_ref)
        return PlanParticipant(plan, participant)

    def get_applicable_rule(
        self,
        ctx: RequestContext,
        plan_id: str,
        *,
        effective_date: date,
        contribution_type: ContributionType,
    ) -> RuleSelection:
        ctx.require(Scope.PLANS_READ)
        plan = self._visible_plan(ctx.principal.tenants, plan_id)
        rules = self._planadmin.list_plan_rules(ctx.principal.tenants, plan.plan_id)
        return select_applicable_rule(
            rules, plan_id=plan.plan_id, on=effective_date, contribution_type=contribution_type
        )

    def get_enrollments(
        self, ctx: RequestContext, plan_id: str, participant_ref: str
    ) -> list[Enrollment]:
        ctx.require(Scope.PLANS_READ)
        pp = self.resolve_plan_participant(ctx, plan_id, participant_ref)
        return self._planadmin.list_enrollments(
            ctx.principal.tenants, pp.participant.participant_ref, pp.plan.plan_id
        )


@dataclass(frozen=True, slots=True)
class ContributionRows:
    pay_period: PayPeriod
    rows: list[PayrollRow]


class ContributionService:
    def __init__(
        self, plans: PlanService, planadmin: PlanAdminReader, payroll: PayrollReader
    ) -> None:
        self._plans, self._planadmin, self._payroll = plans, planadmin, payroll

    def _period(self, pay_period_id: str) -> PayPeriod:
        period = self._payroll.get_pay_period(pay_period_id)
        if period is None:
            raise NotFound("pay_period", pay_period_id)
        return period

    def list_rows(
        self, ctx: RequestContext, plan_id: str, participant_ref: str, pay_period_id: str
    ) -> ContributionRows:
        ctx.require(Scope.PAYROLL_READ)
        pp = self._plans.resolve_plan_participant(ctx, plan_id, participant_ref)
        period = self._period(pay_period_id)
        rows = self._payroll.list_payroll_rows(
            ctx.principal.tenants,
            participant_ref=pp.participant.participant_ref,
            plan_id=pp.plan.plan_id,
            pay_period_id=period.pay_period_id,
        )
        return ContributionRows(period, rows)

    def get_finding(
        self, ctx: RequestContext, plan_id: str, participant_ref: str, pay_period_id: str
    ) -> ContributionFinding:
        ctx.require(Scope.PAYROLL_READ, Scope.PLANS_READ)
        scope = ctx.principal.tenants
        pp = self._plans.resolve_plan_participant(ctx, plan_id, participant_ref)
        period = self._period(pay_period_id)
        ref, pid = pp.participant.participant_ref, pp.plan.plan_id
        return compute_contribution_finding(
            participant_ref=ref,
            plan_id=pid,
            period=period,
            enrollments=self._planadmin.list_enrollments(scope, ref, pid),
            rules=self._planadmin.list_plan_rules(scope, pid),
            payroll_rows=self._payroll.list_payroll_rows(
                scope, participant_ref=ref, plan_id=pid, pay_period_id=period.pay_period_id
            ),
        )
