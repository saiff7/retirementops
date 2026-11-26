"""Fixed ticket taxonomy and the default (fictional) routing table. Deterministic; the LLM never decides."""

from __future__ import annotations

from enum import StrEnum


class Intent(StrEnum):
    CONTRIBUTION_DISCREPANCY = "CONTRIBUTION_DISCREPANCY"
    ENROLLMENT_STATUS = "ENROLLMENT_STATUS"
    ELIGIBILITY_QUESTION = "ELIGIBILITY_QUESTION"
    PLAN_RULE_QUESTION = "PLAN_RULE_QUESTION"
    PAYROLL_FILE_ISSUE = "PAYROLL_FILE_ISSUE"
    ACCESS_OR_PORTAL_ISSUE = "ACCESS_OR_PORTAL_ISSUE"
    GENERAL_INFORMATION = "GENERAL_INFORMATION"
    UNKNOWN_OR_SENSITIVE = "UNKNOWN_OR_SENSITIVE"


class Queue(StrEnum):
    PAYROLL_OPERATIONS = "PAYROLL_OPERATIONS"
    ENROLLMENT_OPERATIONS = "ENROLLMENT_OPERATIONS"
    PLAN_ADMINISTRATION = "PLAN_ADMINISTRATION"
    DIGITAL_SUPPORT = "DIGITAL_SUPPORT"
    HUMAN_TRIAGE = "HUMAN_TRIAGE"


# Spec §4 rules plus three project decisions for intents the spec leaves unmapped. The table and
# rationale are in docs/business-process-as-is-to-be.md §2.3a; tests/unit/test_routing_doc.py keeps the
# two in sync. This is the default route only: low confidence, sensitivity or a blocking finding still
# sends work to manual review (see the rules engine, M3).
DEFAULT_ROUTING: dict[Intent, Queue] = {
    Intent.CONTRIBUTION_DISCREPANCY: Queue.PAYROLL_OPERATIONS,
    Intent.PAYROLL_FILE_ISSUE: Queue.PAYROLL_OPERATIONS,
    Intent.ENROLLMENT_STATUS: Queue.ENROLLMENT_OPERATIONS,
    Intent.ELIGIBILITY_QUESTION: Queue.PLAN_ADMINISTRATION,
    Intent.PLAN_RULE_QUESTION: Queue.PLAN_ADMINISTRATION,
    Intent.GENERAL_INFORMATION: Queue.PLAN_ADMINISTRATION,
    Intent.ACCESS_OR_PORTAL_ISSUE: Queue.DIGITAL_SUPPORT,
    Intent.UNKNOWN_OR_SENSITIVE: Queue.HUMAN_TRIAGE,
}


class TicketStatus(StrEnum):
    NEW = "new"
    OPEN = "open"
    PENDING = "pending"
    SOLVED = "solved"
    CLOSED = "closed"


TERMINAL_TICKET_STATUSES = frozenset({TicketStatus.SOLVED, TicketStatus.CLOSED})
