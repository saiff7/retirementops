"""Stable connector interfaces. Vendor (mock) details stay behind these.

Deliberately absent: any method that closes or solves tickets, changes ticket status, deletes data,
changes enrollment or contributions, or moves money. `tests/unit/test_connector_interfaces.py`
enforces this against FORBIDDEN_METHOD_PATTERN for every interface and adapter.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from retirementops.domain.authz import TenantScope
from retirementops.domain.payroll_upload import ParsedPayrollRow
from retirementops.domain.records import (
    Enrollment,
    Participant,
    PayPeriod,
    PayrollRow,
    Plan,
    PlanRule,
    PolicyCategory,
    PolicySnippet,
    Tenant,
    Ticket,
)
from retirementops.domain.taxonomy import Queue, TicketStatus

FORBIDDEN_METHOD_PATTERN = re.compile(
    r"close|solve|delete|remove|status|send|transfer|refund|pay_out|withdraw|"
    r"modify_enrollment|change_contribution|set_contribution|update_enrollment|execute_sql|raw_sql",
    re.IGNORECASE,
)


class ConnectorError(Exception):
    """Base class for connector failures."""


class StaleRecordError(ConnectorError):
    """Optimistic-concurrency check failed: the record changed since it was read."""


class NotVisibleError(ConnectorError):
    """Record does not exist, or exists outside the caller's tenant scope (indistinguishable)."""


@dataclass(frozen=True, slots=True)
class PostedComment:
    comment_id: int
    ticket_id: str
    idempotency_key: str
    created_at: datetime
    replayed: bool  # True when an earlier call with the same key already posted it


class TicketReader(Protocol):
    def search_tickets(
        self,
        scope: TenantScope,
        *,
        query: str | None,
        status: TicketStatus | None,
        priority: str | None,
        limit: int,
    ) -> list[Ticket]: ...

    def get_ticket(self, scope: TenantScope, ticket_id: str) -> Ticket | None: ...


class TicketWriter(Protocol):
    """Used only by the execution service after human approval (M5). Two narrow writes, nothing else."""

    def post_public_reply(
        self,
        scope: TenantScope,
        *,
        ticket_id: str,
        body: str,
        idempotency_key: str,
        expected_version: int,
    ) -> PostedComment: ...

    def apply_assignment(
        self, scope: TenantScope, *, ticket_id: str, queue: Queue, expected_version: int
    ) -> Ticket: ...


class PlanAdminReader(Protocol):
    def get_tenant(self, scope: TenantScope, tenant_id: str) -> Tenant | None: ...
    def get_plan(self, scope: TenantScope, plan_id: str) -> Plan | None: ...
    def list_plans(self, scope: TenantScope) -> list[Plan]: ...
    def list_plan_rules(self, scope: TenantScope, plan_id: str) -> list[PlanRule]: ...
    def get_participant(self, scope: TenantScope, participant_ref: str) -> Participant | None: ...
    def find_participants_by_employee_ref(
        self, scope: TenantScope, employer_employee_ref: str
    ) -> list[Participant]: ...
    def list_enrollments(
        self, scope: TenantScope, participant_ref: str, plan_id: str
    ) -> list[Enrollment]: ...

    def get_plan_rule(self, scope: TenantScope, rule_id: str) -> PlanRule | None: ...

    def get_enrollment(self, scope: TenantScope, enrollment_id: str) -> Enrollment | None: ...


class PayrollReader(Protocol):
    def get_pay_period(self, pay_period_id: str) -> PayPeriod | None: ...
    def list_pay_periods(self) -> list[PayPeriod]: ...
    def list_payroll_rows(
        self, scope: TenantScope, *, participant_ref: str, plan_id: str, pay_period_id: str
    ) -> list[PayrollRow]: ...

    def get_payroll_row(
        self, scope: TenantScope, file_id: str, row_id: int
    ) -> PayrollRow | None: ...


class PayrollIngestor(Protocol):
    def ingest_file(
        self,
        scope: TenantScope,
        *,
        tenant_id: str,
        plan_id: str,
        pay_period_id: str,
        file_sha256: str,
        rows: Sequence[ParsedPayrollRow],
        received_at: datetime,
    ) -> str: ...


class PolicyKbReader(Protocol):
    """Provider-wide fictional service policies. Read only; effective-dated retrieval."""

    def search_snippets(
        self, *, query: str | None, category: PolicyCategory | None, on: date, limit: int
    ) -> list[PolicySnippet]: ...

    def get_snippet(self, snippet_id: str) -> PolicySnippet | None: ...
