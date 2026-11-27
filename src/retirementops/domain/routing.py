"""Deterministic routing rules engine (spec §4, business-process §2.3a). Pure: no I/O, no model.

The model may only *suggest* an intent and a confidence. This module decides the queue and whether a
human must look first. Risk flags come from host detectors or the model, and they can only make the
outcome more conservative: no input here can route a flagged ticket away from manual review.

Rules, in order:
1. Any human-triage flag, an UNKNOWN_OR_SENSITIVE intent or confidence below the threshold
   -> HUMAN_TRIAGE, manual review.
2. Otherwise the default queue for the intent (DEFAULT_ROUTING).
3. A blocking finding or a possible duplicate keeps that queue but requires manual review.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from retirementops.domain.contributions import BLOCKING_STATUSES, FindingStatus
from retirementops.domain.taxonomy import DEFAULT_ROUTING, Intent, Queue

ROUTING_RULES_VERSION = "routing-rules/1"
DEFAULT_CONFIDENCE_THRESHOLD = 0.70


class RiskFlag(StrEnum):
    PROMPT_INJECTION = "PROMPT_INJECTION"
    CROSS_TENANT_REFERENCE = "CROSS_TENANT_REFERENCE"
    AMBIGUOUS_PARTICIPANT = "AMBIGUOUS_PARTICIPANT"
    UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"
    CONTAINS_RESTRICTED_PATTERN = "CONTAINS_RESTRICTED_PATTERN"
    REQUESTS_CLOSURE = "REQUESTS_CLOSURE"
    POSSIBLE_DUPLICATE = "POSSIBLE_DUPLICATE"


# Flags that send the ticket to HUMAN_TRIAGE. Matches the seed's ground-truth labelling
# (tests/unit/test_routing_engine.py keeps the two in sync).
HUMAN_TRIAGE_FLAGS = frozenset(
    {
        RiskFlag.PROMPT_INJECTION,
        RiskFlag.CROSS_TENANT_REFERENCE,
        RiskFlag.AMBIGUOUS_PARTICIPANT,
        RiskFlag.UNSUPPORTED_ACTION,
        RiskFlag.CONTAINS_RESTRICTED_PATTERN,
        RiskFlag.REQUESTS_CLOSURE,
    }
)
# Flags that keep the default queue but still require a human before anything is drafted.
MANUAL_REVIEW_FLAGS = frozenset({RiskFlag.POSSIBLE_DUPLICATE})


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    intent: Intent
    queue: Queue
    requires_manual_review: bool
    reasons: tuple[str, ...]
    rules_version: str = ROUTING_RULES_VERSION

    def permits(self, queue: Queue) -> bool:
        """The only queue a proposal for this ticket may name."""
        return queue is self.queue


def decide_route(
    *,
    intent: Intent,
    confidence: float,
    flags: Iterable[RiskFlag] = (),
    finding_status: FindingStatus | None = None,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> RoutingDecision:
    if not isinstance(intent, Intent):
        raise TypeError("intent must be an Intent")
    for name, value in (("confidence", confidence), ("threshold", threshold)):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{name} must be a number")
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be between 0 and 1")
    flag_set = frozenset(flags)
    for f in flag_set:
        if not isinstance(f, RiskFlag):
            raise TypeError("flags must be RiskFlag values")

    reasons: list[str] = []
    triage = sorted(f.value for f in flag_set & HUMAN_TRIAGE_FLAGS)
    if triage:
        reasons.append("human-triage flags: " + ", ".join(triage))
    if intent is Intent.UNKNOWN_OR_SENSITIVE:
        reasons.append("intent is UNKNOWN_OR_SENSITIVE")
    if confidence < threshold:
        reasons.append(f"classification confidence {confidence:.2f} below {threshold:.2f}")
    if reasons:
        return RoutingDecision(intent, Queue.HUMAN_TRIAGE, True, tuple(reasons))

    queue = DEFAULT_ROUTING[intent]
    manual = sorted(f.value for f in flag_set & MANUAL_REVIEW_FLAGS)
    if manual:
        reasons.append("manual-review flags: " + ", ".join(manual))
    if finding_status is not None and finding_status in BLOCKING_STATUSES:
        reasons.append(f"finding {finding_status.value} blocks an automated draft")
    return RoutingDecision(intent, queue, bool(reasons), tuple(reasons))
