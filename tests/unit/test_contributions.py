"""Deterministic contribution reconciliation. Expected values are computed by hand in comments."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from retirementops.domain.contributions import (
    FindingStatus,
    compute_contribution_finding,
    expected_employee_contribution,
    expected_employer_match,
)
from retirementops.domain.money import Money
from retirementops.domain.records import (
    ContributionType,
    Enrollment,
    EnrollmentStatus,
    PayPeriod,
    PayrollRow,
    PlanRule,
)
from retirementops.domain.rules import RuleSelection, SelectionStatus, select_applicable_rule
from tests.conftest import make_source

PERIOD = PayPeriod(
    "PP-2026-03",
    date(2026, 2, 2),
    date(2026, 2, 15),
    date(2026, 2, 20),
    make_source("PP-2026-03", "pay_period"),
)
D = Decimal


def rule(
    rule_id: str,
    ctype: ContributionType,
    *,
    f: date = date(2025, 1, 1),
    t: date | None = None,
    maxd: str | None = None,
    rate: str | None = None,
    cap: str | None = None,
    plan: str = "PLAN-001",
    version: str = "v1",
) -> PlanRule:
    return PlanRule(
        rule_id,
        plan,
        "TEN-ALDER",
        ctype,
        version,
        "ELECTED_PCT_OF_ELIGIBLE_PAY"
        if ctype is ContributionType.EMPLOYEE_DEFERRAL
        else "MATCH_RATE_UP_TO_CAP_PCT",
        D(maxd) if maxd else None,
        D(rate) if rate else None,
        D(cap) if cap else None,
        "ROUND_HALF_UP",
        f,
        t,
        make_source(rule_id, "plan_rule"),
    )


DEFER = rule("RULE-001", ContributionType.EMPLOYEE_DEFERRAL, maxd="75")
MATCH = rule("RULE-002", ContributionType.EMPLOYER_MATCH, rate="0.50", cap="6")


def enrollment(
    pct: str = "5",
    status: EnrollmentStatus = EnrollmentStatus.ENROLLED,
    f: date = date(2025, 1, 1),
    t: date | None = None,
    eid: str = "ENR-0001",
) -> Enrollment:
    return Enrollment(
        eid,
        "PART-0001",
        "PLAN-001",
        "TEN-ALDER",
        status,
        D(pct),
        f,
        t,
        make_source(eid, "enrollment"),
    )


def row(pay: int, ee: int, er: int, row_id: int = 1) -> PayrollRow:
    return PayrollRow(
        row_id,
        "PF-1",
        "PART-0001",
        "PLAN-001",
        "TEN-ALDER",
        "PP-2026-03",
        Money.from_cents(pay),
        Money.from_cents(ee),
        Money.from_cents(er),
        make_source(f"PF-1#{row_id}", "payroll_row"),
    )


def finding(enrollments: list[Enrollment], rules: list[PlanRule], rows: list[PayrollRow]):  # type: ignore[no-untyped-def]
    return compute_contribution_finding(
        participant_ref="PART-0001",
        plan_id="PLAN-001",
        period=PERIOD,
        enrollments=enrollments,
        rules=rules,
        payroll_rows=rows,
    )


# ---------------------------------------------------------------- pure formulas
def test_expected_amounts_hand_computed() -> None:
    # 2,000.00 × 5% = 100.00 ; match 0.5 × min(5, 6)% × 2,000.00 = 50.00
    pay = Money.from_cents(200_000)
    assert expected_employee_contribution(pay, D("5"), D("75")) == Money(D("100.00"))
    assert expected_employer_match(pay, D("5"), D("75"), D("0.50"), D("6")) == Money(D("50.00"))
    # 1,234.57 × 7.5% = 92.59275 -> 92.59 ; match 1.0 × min(7.5, 4)% = 49.3828 -> 49.38
    pay = Money.from_cents(123_457)
    assert expected_employee_contribution(pay, D("7.5"), D("75")).to_cents() == 9_259
    assert expected_employer_match(pay, D("7.5"), D("75"), D("1.00"), D("4")).to_cents() == 4_938


def test_half_up_applies_once_at_the_end() -> None:
    # 0.10 × 5% = 0.005 -> 0.01 (rounded once, half up)
    assert expected_employee_contribution(Money.from_cents(10), D("5"), D("75")).to_cents() == 1


def test_max_deferral_cap_limits_employee_and_match() -> None:
    pay = Money.from_cents(100_000)  # 1,000.00
    # elected 70% capped at 60% -> 600.00 ; match 0.5 × min(60, 6)% -> 30.00
    assert expected_employee_contribution(pay, D("70"), D("60")).to_cents() == 60_000
    assert expected_employer_match(pay, D("70"), D("60"), D("0.50"), D("6")).to_cents() == 3_000


# ---------------------------------------------------------------- findings
def test_contribution_finding_match() -> None:
    f = finding([enrollment()], [DEFER, MATCH], [row(200_000, 10_000, 5_000)])
    assert f.status is FindingStatus.MATCH
    assert not f.is_blocking
    assert [c.variance.to_cents() for c in f.components] == [0, 0]
    assert {c.rule_version for c in f.components} == {"v1"}
    assert f.determination_date == "2026-02-15"
    record_types = [e.record_type for e in f.evidence_refs]
    assert record_types == ["pay_period", "enrollment", "plan_rule", "plan_rule", "payroll_row"]


@pytest.mark.parametrize(
    ("ee", "er"), [(9_999, 5_000), (10_001, 5_000), (10_000, 4_999), (10_000, 5_001)]
)
def test_one_cent_variance_is_discrepancy(ee: int, er: int) -> None:
    f = finding([enrollment()], [DEFER, MATCH], [row(200_000, ee, er)])
    assert f.status is FindingStatus.DISCREPANCY
    variances = sorted(abs(c.variance.to_cents()) for c in f.components)
    assert variances == [0, 1]


def test_contribution_finding_discrepancy_reports_signed_variance() -> None:
    f = finding([enrollment()], [DEFER, MATCH], [row(200_000, 7_500, 5_000)])
    ee = f.components[0]
    assert f.status is FindingStatus.DISCREPANCY
    assert (ee.expected.to_cents(), ee.reported.to_cents(), ee.variance.to_cents()) == (
        10_000,
        7_500,
        -2_500,
    )


def test_contribution_finding_missing_row() -> None:
    f = finding([enrollment()], [DEFER, MATCH], [])
    assert f.status is FindingStatus.MISSING_PAYROLL_ROW
    assert f.is_blocking and f.components == ()


def test_multiple_rows_block() -> None:
    f = finding(
        [enrollment()],
        [DEFER, MATCH],
        [row(200_000, 10_000, 5_000, 1), row(200_000, 10_000, 5_000, 2)],
    )
    assert f.status is FindingStatus.MULTIPLE_PAYROLL_ROWS


@pytest.mark.parametrize(
    "status", [EnrollmentStatus.NOT_ENROLLED, EnrollmentStatus.OPTED_OUT, EnrollmentStatus.PENDING]
)
def test_contribution_finding_not_enrolled(status: EnrollmentStatus) -> None:
    f = finding([enrollment("0", status)], [DEFER, MATCH], [row(200_000, 100, 50)])
    assert f.status is FindingStatus.NOT_ENROLLED
    assert any("not enrolled" in r for r in f.reasons)


def test_no_enrollment_on_date_is_not_enrolled() -> None:
    f = finding([enrollment(t=date(2026, 1, 1))], [DEFER, MATCH], [])
    assert f.status is FindingStatus.NOT_ENROLLED


def test_enrollment_conflict() -> None:
    f = finding(
        [enrollment("6", eid="ENR-0001"), enrollment("7", eid="ENR-0002")],
        [DEFER, MATCH],
        [row(200_000, 12_000, 6_000)],
    )
    assert f.status is FindingStatus.ENROLLMENT_CONFLICT


def test_rule_conflict_detected() -> None:
    other = rule("RULE-003", ContributionType.EMPLOYER_MATCH, rate="1.00", cap="4", version="v2")
    f = finding([enrollment()], [DEFER, MATCH, other], [row(200_000, 10_000, 5_000)])
    assert f.status is FindingStatus.RULE_CONFLICT
    assert "RULE-002" in f.reasons[0] and "RULE-003" in f.reasons[0]


def test_rule_missing_detected() -> None:
    f = finding([enrollment()], [DEFER], [row(200_000, 10_000, 0)])
    assert f.status is FindingStatus.RULE_MISSING
    assert "EMPLOYER_MATCH" in f.reasons[0]


def test_rule_selection_half_open_interval() -> None:
    v1 = rule("RULE-002", ContributionType.EMPLOYER_MATCH, rate="0.5", cap="6", t=date(2026, 3, 1))
    v2 = rule(
        "RULE-003",
        ContributionType.EMPLOYER_MATCH,
        rate="1.0",
        cap="4",
        f=date(2026, 3, 1),
        version="v2",
    )

    def pick(on: date) -> RuleSelection:
        return select_applicable_rule(
            [v1, v2], plan_id="PLAN-001", on=on, contribution_type=ContributionType.EMPLOYER_MATCH
        )

    assert pick(date(2026, 2, 28)).rule is v1
    assert pick(date(2026, 3, 1)).rule is v2  # effective_to is exclusive
    assert pick(date(2024, 12, 31)).status is SelectionStatus.MISSING


def test_rule_for_other_plan_is_a_caller_bug() -> None:
    with pytest.raises(ValueError, match="different plan"):
        select_applicable_rule(
            [rule("RULE-9", ContributionType.EMPLOYER_MATCH, rate="1", cap="1", plan="PLAN-002")],
            plan_id="PLAN-001",
            on=date(2026, 1, 1),
            contribution_type=ContributionType.EMPLOYER_MATCH,
        )


def test_rows_for_other_participant_raise() -> None:
    foreign = PayrollRow(
        1,
        "PF-1",
        "PART-0002",
        "PLAN-001",
        "TEN-ALDER",
        "PP-2026-03",
        Money.from_cents(1),
        Money.from_cents(0),
        Money.from_cents(0),
        make_source(),
    )
    with pytest.raises(ValueError, match="does not belong"):
        finding([enrollment()], [DEFER, MATCH], [foreign])
