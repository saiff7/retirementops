"""Server-side draft validation (M3, ADR-013 decision 6). Runs before every draft insert.

Ground truth is rebuilt here from the server's own reads, never from anything the caller asserts:
1. The verified evidence must concern a single participant, plan and pay period.
2. If it identifies all three, the contribution finding is recomputed deterministically
   (`ContributionService.get_finding`), and every record the calculation used must be cited.
3. The pure validator (`domain.draft_validator`) checks the body against that truth.
4. A ticket whose text contains a restricted pattern always needs a human (data-classification §6).

A passing report lets the draft be stored as PENDING_REVIEW; anything else is NEEDS_MANUAL_REVIEW
with value-free reasons. Either way a human approves before anything is sent.
"""

from __future__ import annotations

from collections.abc import Sequence

from retirementops.domain.contributions import ContributionFinding
from retirementops.domain.draft_validator import (
    CitedRecord,
    Problem,
    ValidationIssue,
    ValidationReport,
    build_ground_truth,
    evidence_completeness,
    evidence_subject,
    validate_draft,
)
from retirementops.domain.records import PayPeriod, Ticket
from retirementops.governance.pseudonym import contains_restricted
from retirementops.services.business import ContributionService
from retirementops.services.context import RequestContext
from retirementops.services.errors import Forbidden, NotFound


class DraftValidationService:
    def __init__(self, contributions: ContributionService) -> None:
        self._contributions = contributions

    def validate(
        self, ctx: RequestContext, *, ticket: Ticket, body: str, cited: Sequence[CitedRecord]
    ) -> ValidationReport:
        extra: list[ValidationIssue] = []
        if contains_restricted(ticket.subject) or contains_restricted(ticket.body):
            extra.append(
                ValidationIssue(
                    Problem.TICKET_CONTAINS_RESTRICTED_PATTERN,
                    "the ticket contains a restricted identifier pattern; a human must reply",
                )
            )
        subject = evidence_subject(cited)
        extra += subject.issues

        periods: list[PayPeriod] = []
        finding: ContributionFinding | None = None
        try:
            if subject.pay_period_id:
                periods.append(self._contributions.get_pay_period(ctx, subject.pay_period_id))
            if subject.participant_ref and subject.plan_id and subject.pay_period_id:
                finding = self._contributions.get_finding(
                    ctx, subject.plan_id, subject.participant_ref, subject.pay_period_id
                )
        except (NotFound, Forbidden):
            extra.append(
                ValidationIssue(
                    Problem.EVIDENCE_SUBJECT_UNRESOLVED,
                    "the participant, plan or pay period in the evidence could not be resolved",
                )
            )
        if finding is not None:
            extra += evidence_completeness(cited, finding)

        truth = build_ground_truth(
            ticket_id=ticket.ticket_id,
            finding=finding,
            pay_periods=periods,
            participant_refs=[subject.participant_ref] if subject.participant_ref else [],
            plan_ids=[subject.plan_id] if subject.plan_id else [],
        )
        return validate_draft(body, truth).with_issues(extra)
