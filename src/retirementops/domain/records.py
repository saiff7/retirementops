"""Business records as returned by connectors. Frozen, minimal, each with provenance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from retirementops.domain.evidence import SourceRef
from retirementops.domain.money import Money
from retirementops.domain.taxonomy import Queue, TicketStatus


def effective_on(effective_from: date, effective_to: date | None, on: date) -> bool:
    """Half-open interval [effective_from, effective_to). `None` means open-ended."""
    return effective_from <= on and (effective_to is None or on < effective_to)


@dataclass(frozen=True, slots=True)
class Tenant:
    tenant_id: str
    display_name: str
    source: SourceRef


@dataclass(frozen=True, slots=True)
class Plan:
    plan_id: str
    tenant_id: str
    name: str
    currency: str
    source: SourceRef


class ContributionType(StrEnum):
    EMPLOYEE_DEFERRAL = "EMPLOYEE_DEFERRAL"
    EMPLOYER_MATCH = "EMPLOYER_MATCH"


@dataclass(frozen=True, slots=True)
class PlanRule:
    """Fictional demonstration rule. Percentages are in percent units (5.0 = 5%)."""

    rule_id: str
    plan_id: str
    tenant_id: str
    contribution_type: ContributionType
    rule_version: str
    formula_code: str
    max_deferral_pct: Decimal | None
    match_rate: Decimal | None  # fraction: 0.50 = 50 cents per matched dollar
    match_cap_pct: Decimal | None  # percent of eligible pay that is matched
    rounding: str
    effective_from: date
    effective_to: date | None
    source: SourceRef

    def is_effective_on(self, on: date) -> bool:
        return effective_on(self.effective_from, self.effective_to, on)


@dataclass(frozen=True, slots=True)
class Participant:
    participant_ref: str
    tenant_id: str
    source: SourceRef


class EnrollmentStatus(StrEnum):
    ENROLLED = "ENROLLED"
    NOT_ENROLLED = "NOT_ENROLLED"
    OPTED_OUT = "OPTED_OUT"
    PENDING = "PENDING"


@dataclass(frozen=True, slots=True)
class Enrollment:
    enrollment_id: str
    participant_ref: str
    plan_id: str
    tenant_id: str
    status: EnrollmentStatus
    deferral_pct: Decimal
    effective_from: date
    effective_to: date | None
    source: SourceRef

    def is_effective_on(self, on: date) -> bool:
        return effective_on(self.effective_from, self.effective_to, on)


@dataclass(frozen=True, slots=True)
class PayPeriod:
    pay_period_id: str
    period_start: date
    period_end: date
    pay_date: date
    source: SourceRef


@dataclass(frozen=True, slots=True)
class PayrollRow:
    row_id: int
    file_id: str
    participant_ref: str
    plan_id: str
    tenant_id: str
    pay_period_id: str
    eligible_pay: Money
    employee_contribution: Money
    employer_contribution: Money
    source: SourceRef


@dataclass(frozen=True, slots=True)
class Ticket:
    ticket_id: str
    tenant_id: str
    org_id: str
    subject: str
    body: str  # UNTRUSTED content
    status: TicketStatus
    priority: str
    assignee_queue: Queue | None
    channel: str
    created_at: datetime
    updated_at: datetime
    version: int
    source: SourceRef
