"""Draft validator: every checkable claim must come from host-fetched ground truth (T-07, T-08)."""

from __future__ import annotations

import pytest

from retirementops.domain.contributions import FindingStatus
from retirementops.domain.draft_validator import (
    VALIDATOR_VERSION,
    GroundTruth,
    Problem,
    build_ground_truth,
    validate_draft,
)
from tests.unit.test_contributions import DEFER, MATCH, PERIOD, enrollment, finding, row

# eligible pay $2,000.00 at 5%: expected employee $100.00 (reported $99.00, variance -$1.00);
# expected match $50.00 (reported $50.00).
DISCREPANCY = finding([enrollment("5")], [DEFER, MATCH], [row(200_000, 9_900, 5_000)])
MATCHED = finding([enrollment("5")], [DEFER, MATCH], [row(200_000, 10_000, 5_000)])
MISSING_ROW = finding([enrollment("5")], [DEFER, MATCH], [])

GOOD = (
    "Thank you for your message about participant PART-0001 in plan PLAN-001 for pay period "
    "PP-2026-03 (ending 2026-02-15). The payroll file we received reports an employee contribution "
    "of $99.00. Under the plan's fictional rule at a deferral rate of 5.00%, the expected employee "
    "contribution on eligible pay of $2,000.00 is $100.00, a difference of $1.00. The employer "
    "match reported, $50.00, equals the expected amount. Our payroll operations team will review "
    "this finding with you before anything else happens."
)


def truth(f=DISCREPANCY) -> GroundTruth:  # type: ignore[no-untyped-def]
    return build_ground_truth(ticket_id="ZD-1001", finding=f, pay_periods=[PERIOD])


def codes(body: str, t: GroundTruth | None = None) -> set[Problem]:
    return {i.code for i in validate_draft(body, t or truth()).issues}


def test_assumptions_about_the_fixture() -> None:
    assert DISCREPANCY.status is FindingStatus.DISCREPANCY
    assert MATCHED.status is FindingStatus.MATCH
    assert MISSING_ROW.status is FindingStatus.MISSING_PAYROLL_ROW
    t = truth()
    assert {"$100.00", "$99.00", "-$1.00", "$1.00", "$2,000.00", "$50.00", "$0.00"} <= t.amounts
    assert t.percents == {"5.00%"}
    assert {"2026-02-02", "2026-02-15", "2026-02-20"} <= t.dates


def test_good_draft_passes() -> None:
    report = validate_draft(GOOD, truth())
    assert report.passed, report.reasons
    assert report.validator_version == VALIDATOR_VERSION


# ---------------------------------------------------------------- amounts (T-07)
@pytest.mark.parametrize("amount", ["$101.00", "$99.99", "$1.01", "-$2.00", "$0.01"])
def test_invented_amount_is_rejected(amount: str) -> None:
    assert Problem.UNSUPPORTED_AMOUNT in codes(GOOD.replace("$100.00", amount))


@pytest.mark.parametrize(
    "spelling", ["$100", "$100.0", "$ 100.00", "100.00", "100 dollars", "100.00 USD", "$1000.00"]
)
def test_noncanonical_amount_spelling_is_rejected(spelling: str) -> None:
    assert Problem.NONCANONICAL_AMOUNT in codes(GOOD.replace("$100.00", spelling))


def test_any_amount_is_rejected_when_there_is_no_finding() -> None:
    t = build_ground_truth(ticket_id="ZD-1001", plan_ids=["PLAN-001"])
    assert Problem.UNSUPPORTED_AMOUNT in codes("Your plan PLAN-001 match is $50.00.", t)
    assert validate_draft("We received your question about plan PLAN-001.", t).passed


@pytest.mark.parametrize("pct", ["6.00%", "5%", "5.0%", "50.00%"])
def test_unsupported_percentage_is_rejected(pct: str) -> None:
    assert Problem.UNSUPPORTED_PERCENT in codes(GOOD.replace("5.00%", pct))


# ---------------------------------------------------------------- dates
def test_date_outside_the_evidence_is_rejected() -> None:
    assert Problem.UNSUPPORTED_DATE in codes(GOOD.replace("2026-02-15", "2026-02-28"))


@pytest.mark.parametrize("date_text", ["February 15", "Feb 15th", "15 Feb", "2/15/2026"])
def test_noncanonical_date_is_rejected(date_text: str) -> None:
    assert Problem.NONCANONICAL_DATE in codes(GOOD.replace("2026-02-15", date_text))


# ---------------------------------------------------------------- identifiers (T-08)
def test_foreign_participant_ref_is_rejected() -> None:
    assert Problem.FOREIGN_PARTICIPANT in codes(GOOD + " See also PART-0030.")


@pytest.mark.parametrize(
    ("extra", "code"),
    [
        ("Plan PLAN-003 differs.", Problem.FOREIGN_PLAN),
        ("Compare PP-2026-04.", Problem.FOREIGN_PAY_PERIOD),
        ("Related to ZD-1002.", Problem.FOREIGN_TICKET),
        ("Employee E-40404.", Problem.EMPLOYEE_REF_PRESENT),
        ("Tenant TEN-ALDER.", Problem.TENANT_ID_PRESENT),
    ],
)
def test_other_identifiers_outside_the_resolved_set_are_rejected(extra: str, code: Problem) -> None:
    assert code in codes(GOOD + " " + extra)


def test_identifier_digits_are_not_mistaken_for_amounts_or_dates() -> None:
    body = "Ticket ZD-1001 concerns PART-0001, PLAN-001 and PP-2026-03."
    assert validate_draft(body, truth()).passed


# ---------------------------------------------------------------- claims
@pytest.mark.parametrize(
    "claim",
    [
        "We have corrected the contribution.",
        "We've refunded the difference.",
        "I updated the deferral.",
        "The contribution has been adjusted.",
        "This ticket is now closed.",
        "A refund has been issued.",
        "A correction will be applied next payroll.",
        "We will credit the account.",
        "The amount was corrected yesterday.",
    ],
)
def test_claims_of_unsupported_actions_are_rejected(claim: str) -> None:
    assert Problem.UNSUPPORTED_ACTION_CLAIM in codes(GOOD + " " + claim)


@pytest.mark.parametrize(
    "harmless",
    [
        "Contributions are processed each pay period.",
        "Our team will review this with you.",
        "We received your message.",
    ],
)
def test_ordinary_sentences_are_not_flagged_as_actions(harmless: str) -> None:
    assert validate_draft(GOOD + " " + harmless, truth()).passed


def test_draft_contradicting_a_discrepancy_is_rejected() -> None:
    assert Problem.CONTRADICTS_FINDING in codes(GOOD + " There is no discrepancy.")


def test_draft_reporting_a_discrepancy_on_a_match_is_rejected() -> None:
    body = "We found a discrepancy for PART-0001 in PP-2026-03."
    assert Problem.CONTRADICTS_FINDING in codes(body, truth(MATCHED))


def test_blocking_finding_fails_validation() -> None:
    body = "We are looking into PART-0001 for PP-2026-03."
    report = validate_draft(body, truth(MISSING_ROW))
    assert not report.passed
    assert {i.code for i in report.issues} == {Problem.FINDING_BLOCKS_DRAFT}


def test_empty_and_oversized_bodies_fail() -> None:
    assert Problem.EMPTY_BODY in codes("   ")
    assert Problem.BODY_TOO_LONG in codes("a" * 10_001)


def test_reasons_never_echo_draft_values() -> None:
    bad = GOOD.replace("$100.00", "$123.45") + " See PART-0030 and E-40404."
    report = validate_draft(bad, truth())
    joined = " ".join(report.reasons)
    for value in ("123.45", "PART-0030", "E-40404"):
        assert value not in joined


def test_non_string_body_is_a_type_error() -> None:
    with pytest.raises(TypeError):
        validate_draft(None, truth())  # type: ignore[arg-type]
