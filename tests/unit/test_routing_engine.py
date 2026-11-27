"""Routing rules engine: deterministic, conservative, and consistent with the seed's ground truth."""

from __future__ import annotations

import math

import pytest

from retirementops.domain.contributions import BLOCKING_STATUSES, FindingStatus
from retirementops.domain.routing import (
    HUMAN_TRIAGE_FLAGS,
    MANUAL_REVIEW_FLAGS,
    ROUTING_RULES_VERSION,
    RiskFlag,
    decide_route,
)
from retirementops.domain.taxonomy import DEFAULT_ROUTING, Intent, Queue
from retirementops.seed import generator


def test_clean_ticket_uses_default_route_without_manual_review() -> None:
    d = decide_route(intent=Intent.CONTRIBUTION_DISCREPANCY, confidence=0.9)
    assert (d.queue, d.requires_manual_review, d.reasons) == (Queue.PAYROLL_OPERATIONS, False, ())
    assert d.rules_version == ROUTING_RULES_VERSION
    assert d.permits(Queue.PAYROLL_OPERATIONS)
    assert not d.permits(Queue.HUMAN_TRIAGE)


@pytest.mark.parametrize("intent", list(Intent))
def test_every_intent_routes_to_its_default_queue(intent: Intent) -> None:
    d = decide_route(intent=intent, confidence=1.0)
    assert d.queue is DEFAULT_ROUTING[intent]
    assert d.requires_manual_review is (intent is Intent.UNKNOWN_OR_SENSITIVE)


def test_low_confidence_goes_to_human_triage() -> None:
    d = decide_route(intent=Intent.ENROLLMENT_STATUS, confidence=0.69)
    assert d.queue is Queue.HUMAN_TRIAGE and d.requires_manual_review
    assert "below 0.70" in d.reasons[0]
    # Exactly at the threshold is accepted.
    assert not decide_route(intent=Intent.ENROLLMENT_STATUS, confidence=0.70).requires_manual_review


@pytest.mark.parametrize("flag", sorted(HUMAN_TRIAGE_FLAGS))
def test_each_triage_flag_forces_human_triage_even_at_full_confidence(flag: RiskFlag) -> None:
    d = decide_route(intent=Intent.CONTRIBUTION_DISCREPANCY, confidence=1.0, flags=[flag])
    assert d.queue is Queue.HUMAN_TRIAGE and d.requires_manual_review
    assert flag.value in d.reasons[0]


def test_duplicate_keeps_queue_but_requires_review() -> None:
    d = decide_route(
        intent=Intent.CONTRIBUTION_DISCREPANCY, confidence=1.0, flags=MANUAL_REVIEW_FLAGS
    )
    assert d.queue is Queue.PAYROLL_OPERATIONS and d.requires_manual_review


@pytest.mark.parametrize("status", sorted(BLOCKING_STATUSES))
def test_blocking_finding_requires_review_on_default_queue(status: FindingStatus) -> None:
    d = decide_route(intent=Intent.CONTRIBUTION_DISCREPANCY, confidence=1.0, finding_status=status)
    assert d.queue is Queue.PAYROLL_OPERATIONS and d.requires_manual_review


@pytest.mark.parametrize("status", [FindingStatus.MATCH, FindingStatus.DISCREPANCY])
def test_non_blocking_findings_allow_a_draft(status: FindingStatus) -> None:
    d = decide_route(intent=Intent.CONTRIBUTION_DISCREPANCY, confidence=1.0, finding_status=status)
    assert not d.requires_manual_review


@pytest.mark.parametrize("bad", [-0.01, 1.01, math.nan, math.inf, True, "0.9", None])
def test_invalid_confidence_is_rejected(bad: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        decide_route(intent=Intent.GENERAL_INFORMATION, confidence=bad)  # type: ignore[arg-type]


def test_invalid_intent_or_flag_is_rejected() -> None:
    with pytest.raises(TypeError):
        decide_route(intent="CONTRIBUTION_DISCREPANCY", confidence=1.0)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        decide_route(
            intent=Intent.GENERAL_INFORMATION,
            confidence=1.0,
            flags=["PROMPT_INJECTION"],  # type: ignore[list-item]
        )


def test_triage_flags_match_the_seed_labelling() -> None:
    assert {f.value for f in HUMAN_TRIAGE_FLAGS} == set(generator.HUMAN_TRIAGE_FLAGS)


def test_engine_reproduces_every_seeded_ground_truth_label() -> None:
    """Given the true intent, flags and finding, the engine must reach the labelled outcome."""
    labels = generator.generate(42).ticket_labels
    assert len(labels) >= 40
    for label in labels:
        flags = {RiskFlag(f) for f in label["flags"] if not f.startswith("DUPLICATE_OF:")}
        if any(f.startswith("DUPLICATE_OF:") for f in label["flags"]):
            flags.add(RiskFlag.POSSIBLE_DUPLICATE)
        finding = label["expected_finding"]
        d = decide_route(
            intent=Intent(label["intent"]),
            confidence=1.0,
            flags=flags,
            finding_status=FindingStatus(finding) if finding else None,
        )
        assert d.queue.value == label["expected_queue"], label["ticket_id"]
        assert d.requires_manual_review is label["requires_manual_review"], label["ticket_id"]
