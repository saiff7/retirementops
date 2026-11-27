"""Deterministic risk detectors for requester text (threat model T-01). Pure, no model.

They do not depend on the model noticing anything, and they only ever ADD flags: a flagged ticket
goes to a human. Patterns are deliberately broad; a false positive costs a manual review, a false
negative is still contained by the host and server controls.
"""

from __future__ import annotations

import re

from retirementops.domain.routing import RiskFlag

_INJECTION = re.compile(
    r"(?i)ignore\s+(?:all\s+|your\s+|any\s+|the\s+)?(?:previous|prior|above)?\s*instructions"
    r"|system\s+(?:note|prompt|message)|\bassistant\s*:|<!--|\bdeveloper\s+mode\b"
    r"|you\s+(?:are|have\s+been)\s+authori[sz]ed|(?:skip|bypass|without)\s+(?:the\s+)?review"
    r"|approve\s+your\s+own|no\s+review\s+(?:is\s+)?needed"
)
_CLOSURE = re.compile(
    r"(?i)\bclose\s+(?:this|the|my)\s+(?:ticket|case|request)\b"
    r"|\bmark\s+(?:this|the|it)?\s*(?:ticket\s+|case\s+)?(?:as\s+)?(?:solved|closed|resolved)\b"
    r"|\b(?:and|then)\s+close\s+(?:it|this)\b"
)
_UNSUPPORTED_ACTION = re.compile(
    r"(?i)\b(?:update|delete\s+from|insert\s+into|drop\s+table|truncate)\s+\w+"
    r"|\brun\s+(?:this|the\s+following)\b"
    r"|\b(?:change|set|increase|decrease|update|modify)\s+(?:\S+\s+){0,3}(?:deferral|contribution|"
    r"enrollment|election|rate)s?\b"
    r"|\b(?:send|post)\s+(?:it|this|the\s+reply)\s+(?:immediately|now)\b"
    r"|\b(?:refund|reimburse|wire|transfer)\b"
)


def detect_risk_flags(subject: str, body: str) -> frozenset[RiskFlag]:
    text = f"{subject}\n{body}"
    flags: set[RiskFlag] = set()
    if _INJECTION.search(text):
        flags.add(RiskFlag.PROMPT_INJECTION)
    if _CLOSURE.search(text):
        flags.add(RiskFlag.REQUESTS_CLOSURE)
    if _UNSUPPORTED_ACTION.search(text):
        flags.add(RiskFlag.UNSUPPORTED_ACTION)
    return frozenset(flags)
