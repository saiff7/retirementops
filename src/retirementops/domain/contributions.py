"""Deterministic contribution reconciliation (ADR-006). Pure: no I/O, no clock, no model.

Fictional demonstration rules:
    effective_deferral_pct = min(elected deferral_pct, rule.max_deferral_pct)
    expected_employee      = eligible_pay × effective_deferral_pct / 100
    matched_pct            = min(effective_deferral_pct, rule.match_cap_pct)
    expected_employer      = eligible_pay × matched_pct / 100 × rule.match_rate
Each expected amount is computed exactly, then rounded once to cents with ROUND_HALF_UP.
The rule and enrollment in force are the ones effective on the pay period's END date.

Variance policy (owner decision): variance = reported − expected, per component. ANY non-zero variance
in either component, including 1 cent, makes the finding a DISCREPANCY. There is no tolerance.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from retirementops.domain.evidence import SourceRef
from retirementops.domain.money import (
    ROUNDING_POLICY,
    Money,
    exact_percent,
    exact_product,
    quantize_money,
)
from retirementops.domain.records import (
    ContributionType,
    Enrollment,
    EnrollmentStatus,
    PayPeriod,
    PayrollRow,
    PlanRule,
)
from retirementops.domain.rules import SelectionStatus, select_applicable_rule, select_enrollment

CALC_VERSION = "contribution-calc/1"
EMPLOYEE_FORMULA = "eligible_pay × min(deferral_pct, max_deferral_pct) / 100"
EMPLOYER_FORMULA = "eligible_pay × min(effective_deferral_pct, match_cap_pct) / 100 × match_rate"


class FindingStatus(StrEnum):
    MATCH = "MATCH"
    DISCREPANCY = "DISCREPANCY"
    MISSING_PAYROLL_ROW = "MISSING_PAYROLL_ROW"
    MULTIPLE_PAYROLL_ROWS = "MULTIPLE_PAYROLL_ROWS"
    NOT_ENROLLED = "NOT_ENROLLED"
    ENROLLMENT_CONFLICT = "ENROLLMENT_CONFLICT"
    RULE_MISSING = "RULE_MISSING"
    RULE_CONFLICT = "RULE_CONFLICT"


# Statuses where no amount can be asserted and a human must look before any reply is drafted.
BLOCKING_STATUSES = frozenset(set(FindingStatus) - {FindingStatus.MATCH, FindingStatus.DISCREPANCY})


@dataclass(frozen=True, slots=True)
class ComponentResult:
    component: ContributionType
    rule_id: str
    rule_version: str
    formula: str
    expected: Money
    reported: Money
    variance: Money  # reported − expected


@dataclass(frozen=True, slots=True)
class ContributionFinding:
    status: FindingStatus
    participant_ref: str
    plan_id: str
    pay_period_id: str
    determination_date: str  # ISO date the rules/enrollment were evaluated on (period end)
    calc_version: str
    rounding: str
    eligible_pay: Money | None
    effective_deferral_pct: Decimal | None
    components: tuple[ComponentResult, ...]
    reasons: tuple[str, ...]
    evidence_refs: tuple[SourceRef, ...]

    @property
    def is_blocking(self) -> bool:
        return self.status in BLOCKING_STATUSES


def expected_employee_contribution(
    eligible_pay: Money, deferral_pct: Decimal, max_deferral_pct: Decimal
) -> Money:
    effective = min(deferral_pct, max_deferral_pct)
    return quantize_money(exact_product(eligible_pay.amount, exact_percent(effective)))


def expected_employer_match(
    eligible_pay: Money,
    deferral_pct: Decimal,
    max_deferral_pct: Decimal,
    match_rate: Decimal,
    match_cap_pct: Decimal,
) -> Money:
    effective = min(deferral_pct, max_deferral_pct)
    matched = min(effective, match_cap_pct)
    return quantize_money(exact_product(eligible_pay.amount, exact_percent(matched), match_rate))


def _finding(
    status: FindingStatus,
    *,
    participant_ref: str,
    plan_id: str,
    period: PayPeriod,
    reasons: Sequence[str],
    evidence: Sequence[SourceRef],
    eligible_pay: Money | None = None,
    effective_deferral_pct: Decimal | None = None,
    components: Sequence[ComponentResult] = (),
) -> ContributionFinding:
    return ContributionFinding(
        status=status,
        participant_ref=participant_ref,
        plan_id=plan_id,
        pay_period_id=period.pay_period_id,
        determination_date=period.period_end.isoformat(),
        calc_version=CALC_VERSION,
        rounding=ROUNDING_POLICY,
        eligible_pay=eligible_pay,
        effective_deferral_pct=effective_deferral_pct,
        components=tuple(components),
        reasons=tuple(reasons),
        evidence_refs=tuple(evidence),
    )


def compute_contribution_finding(
    *,
    participant_ref: str,
    plan_id: str,
    period: PayPeriod,
    enrollments: Sequence[Enrollment],
    rules: Sequence[PlanRule],
    payroll_rows: Sequence[PayrollRow],
) -> ContributionFinding:
    """Reconcile one participant / plan / pay period. Inputs must already be tenant-authorised."""
    for row in payroll_rows:
        if (row.participant_ref, row.plan_id, row.pay_period_id) != (
            participant_ref,
            plan_id,
            period.pay_period_id,
        ):
            raise ValueError("payroll row does not belong to this participant/plan/period")

    on = period.period_end
    evidence: list[SourceRef] = [period.source]

    def done(
        status: FindingStatus,
        reasons: Sequence[str],
        *,
        eligible_pay: Money | None = None,
        effective_deferral_pct: Decimal | None = None,
        components: Sequence[ComponentResult] = (),
    ) -> ContributionFinding:
        return _finding(
            status,
            participant_ref=participant_ref,
            plan_id=plan_id,
            period=period,
            reasons=reasons,
            evidence=evidence,
            eligible_pay=eligible_pay,
            effective_deferral_pct=effective_deferral_pct,
            components=components,
        )

    enrollment_sel = select_enrollment(
        enrollments, participant_ref=participant_ref, plan_id=plan_id, on=on
    )
    if enrollment_sel.status is SelectionStatus.CONFLICT:
        evidence += [
            e.source for e in enrollments if e.enrollment_id in enrollment_sel.candidate_ids
        ]
        return done(
            FindingStatus.ENROLLMENT_CONFLICT,
            [f"overlapping enrollments: {', '.join(enrollment_sel.candidate_ids)}"],
        )
    enrollment = enrollment_sel.enrollment
    if enrollment is None or enrollment.status is not EnrollmentStatus.ENROLLED:
        reasons = (
            ["no enrollment effective on period end"]
            if enrollment is None
            else [f"enrollment status is {enrollment.status.value}"]
        )
        if enrollment is not None:
            evidence.append(enrollment.source)
        if payroll_rows:
            reasons.append("payroll reported contributions for a participant who is not enrolled")
            evidence += [r.source for r in payroll_rows]
        return done(FindingStatus.NOT_ENROLLED, reasons)
    evidence.append(enrollment.source)

    deferral_sel = select_applicable_rule(
        rules, plan_id=plan_id, on=on, contribution_type=ContributionType.EMPLOYEE_DEFERRAL
    )
    match_sel = select_applicable_rule(
        rules, plan_id=plan_id, on=on, contribution_type=ContributionType.EMPLOYER_MATCH
    )
    selections = (deferral_sel, match_sel)
    labelled = (
        (ContributionType.EMPLOYEE_DEFERRAL, deferral_sel),
        (ContributionType.EMPLOYER_MATCH, match_sel),
    )
    if any(s.status is SelectionStatus.CONFLICT for s in selections):
        ids = sorted(
            {i for s in selections if s.status is SelectionStatus.CONFLICT for i in s.candidate_ids}
        )
        evidence += [r.source for r in rules if r.rule_id in ids]
        return done(
            FindingStatus.RULE_CONFLICT,
            [f"more than one rule in force on {on.isoformat()}: {', '.join(ids)}"],
        )
    if any(s.status is SelectionStatus.MISSING for s in selections):
        missing = [t.value for t, s in labelled if s.status is SelectionStatus.MISSING]
        return done(
            FindingStatus.RULE_MISSING,
            [f"no rule in force on {on.isoformat()} for {', '.join(missing)}"],
        )
    deferral_rule, match_rule = deferral_sel.rule, match_sel.rule
    if deferral_rule is None or match_rule is None:  # unreachable: statuses checked above
        raise RuntimeError("rule selection invariant violated")
    evidence += [deferral_rule.source, match_rule.source]

    if not payroll_rows:
        return done(
            FindingStatus.MISSING_PAYROLL_ROW,
            ["no payroll row received for this participant, plan and pay period"],
        )
    if len(payroll_rows) > 1:
        evidence += [r.source for r in payroll_rows]
        return done(
            FindingStatus.MULTIPLE_PAYROLL_ROWS,
            [f"{len(payroll_rows)} payroll rows for one pay period; cannot pick one"],
        )
    row = payroll_rows[0]
    evidence.append(row.source)

    if (
        deferral_rule.max_deferral_pct is None
        or match_rule.match_rate is None
        or match_rule.match_cap_pct is None
    ):  # the DB CHECK constraint guarantees these; fail loudly if a connector broke it
        raise ValueError("plan rule is missing required parameters")
    effective_pct = min(enrollment.deferral_pct, deferral_rule.max_deferral_pct)
    exp_ee = expected_employee_contribution(
        row.eligible_pay, enrollment.deferral_pct, deferral_rule.max_deferral_pct
    )
    exp_er = expected_employer_match(
        row.eligible_pay,
        enrollment.deferral_pct,
        deferral_rule.max_deferral_pct,
        match_rule.match_rate,
        match_rule.match_cap_pct,
    )
    components = (
        ComponentResult(
            ContributionType.EMPLOYEE_DEFERRAL,
            deferral_rule.rule_id,
            deferral_rule.rule_version,
            EMPLOYEE_FORMULA,
            exp_ee,
            row.employee_contribution,
            row.employee_contribution - exp_ee,
        ),
        ComponentResult(
            ContributionType.EMPLOYER_MATCH,
            match_rule.rule_id,
            match_rule.rule_version,
            EMPLOYER_FORMULA,
            exp_er,
            row.employer_contribution,
            row.employer_contribution - exp_er,
        ),
    )
    off = [c for c in components if not c.variance.is_zero()]
    status = FindingStatus.DISCREPANCY if off else FindingStatus.MATCH
    reasons = [f"{c.component.value} variance is non-zero" for c in off]
    return done(
        status,
        reasons,
        eligible_pay=row.eligible_pay,
        effective_deferral_pct=effective_pct,
        components=components,
    )
