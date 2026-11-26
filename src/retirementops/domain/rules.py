"""Effective-dated selection of plan rules and enrollments. Exactly one must apply, or we stop."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from retirementops.domain.records import ContributionType, Enrollment, PlanRule


class SelectionStatus(StrEnum):
    SELECTED = "SELECTED"
    MISSING = "MISSING"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True, slots=True)
class RuleSelection:
    status: SelectionStatus
    rule: PlanRule | None
    candidate_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EnrollmentSelection:
    status: SelectionStatus
    enrollment: Enrollment | None
    candidate_ids: tuple[str, ...]


def select_applicable_rule(
    rules: Iterable[PlanRule], *, plan_id: str, on: date, contribution_type: ContributionType
) -> RuleSelection:
    """Select the single rule in force on `on`. Rules for a different plan are a caller bug."""
    matching: list[PlanRule] = []
    for rule in rules:
        if rule.plan_id != plan_id:
            raise ValueError("rule belongs to a different plan")
        if rule.contribution_type is contribution_type and rule.is_effective_on(on):
            matching.append(rule)
    ids = tuple(sorted(r.rule_id for r in matching))
    if not matching:
        return RuleSelection(SelectionStatus.MISSING, None, ids)
    if len(matching) > 1:
        return RuleSelection(SelectionStatus.CONFLICT, None, ids)
    return RuleSelection(SelectionStatus.SELECTED, matching[0], ids)


def select_enrollment(
    enrollments: Iterable[Enrollment], *, participant_ref: str, plan_id: str, on: date
) -> EnrollmentSelection:
    matching: list[Enrollment] = []
    for e in enrollments:
        if e.participant_ref != participant_ref or e.plan_id != plan_id:
            raise ValueError("enrollment belongs to a different participant or plan")
        if e.is_effective_on(on):
            matching.append(e)
    ids = tuple(sorted(e.enrollment_id for e in matching))
    if not matching:
        return EnrollmentSelection(SelectionStatus.MISSING, None, ids)
    if len(matching) > 1:
        return EnrollmentSelection(SelectionStatus.CONFLICT, None, ids)
    return EnrollmentSelection(SelectionStatus.SELECTED, matching[0], ids)
