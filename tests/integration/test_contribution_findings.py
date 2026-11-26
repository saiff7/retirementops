"""End-to-end deterministic findings over the real DB, through services and per-role connectors."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from retirementops.domain.contributions import FindingStatus
from retirementops.seed.generator import Dataset
from retirementops.services.container import Container
from retirementops.services.context import RequestContext

pytestmark = pytest.mark.integration


def test_every_seeded_case_produces_its_expected_finding(
    container: Container, dataset: Dataset, ctx_for: Callable[[str], RequestContext]
) -> None:
    ctx = ctx_for("rev.sam")  # reviewer assigned to all three tenants
    wrong: list[tuple[str, str, str]] = []
    for c in dataset.cases:
        f = container.contributions.get_finding(
            ctx, c["plan_id"], c["participant_ref"], c["pay_period_id"]
        )
        if f.status.value != c["expected_status"]:
            wrong.append((c["case"], c["participant_ref"], f.status.value))
    assert wrong == []


def test_one_cent_cases_have_exactly_one_cent_variance(
    container: Container, dataset: Dataset, ctx_for: Callable[[str], RequestContext]
) -> None:
    ctx = ctx_for("rev.sam")
    for name, sign in (("ONE_CENT_UNDER", -1), ("ONE_CENT_OVER", 1)):
        c = next(x for x in dataset.cases if x["case"] == name)
        f = container.contributions.get_finding(
            ctx, c["plan_id"], c["participant_ref"], c["pay_period_id"]
        )
        assert sorted(v.variance.to_cents() for v in f.components) == sorted([0, sign])


def test_no_unintended_faults_anywhere_in_the_seed(
    container: Container, dataset: Dataset, ctx_for: Callable[[str], RequestContext]
) -> None:
    """Every participant/plan/period NOT listed as a case must reconcile cleanly, or be one of the
    structural conditions the seed documents (rule gaps/conflicts, enrollment conflict, not enrolled).
    """
    ctx = ctx_for("rev.sam")
    case_keys = {(c["participant_ref"], c["plan_id"], c["pay_period_id"]) for c in dataset.cases}
    periods = [p["pay_period_id"] for p in dataset.tables["payroll_mock.pay_periods"]]
    pairs = sorted(
        {(e["participant_ref"], e["plan_id"]) for e in dataset.tables["planadmin_mock.enrollments"]}
    )
    structural = {
        FindingStatus.NOT_ENROLLED,
        FindingStatus.RULE_CONFLICT,
        FindingStatus.RULE_MISSING,
        FindingStatus.ENROLLMENT_CONFLICT,
    }
    seen: dict[str, int] = {}
    unexpected: list[Any] = []
    for ref, plan in pairs:
        for ppid in periods:
            if (ref, plan, ppid) in case_keys:
                continue
            f = container.contributions.get_finding(ctx, plan, ref, ppid)
            seen[f.status.value] = seen.get(f.status.value, 0) + 1
            if f.status is not FindingStatus.MATCH and f.status not in structural:
                unexpected.append((ref, plan, ppid, f.status.value))
    assert unexpected == []
    assert seen["MATCH"] > 300
