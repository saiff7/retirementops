"""The ModelProvider contract (spec §8, ADR-005): structured requests in, validated answers out.

Every answer a model gives is parsed into a strict Pydantic model (`extra="forbid"`, bounded sizes,
fixed enums). A parse failure raises `ProviderOutputError`; the orchestrator retries once, then
fails closed to manual review. Error messages never echo model output.

One deliberate difference from the spec's sketch: `choose_tools` returns a `ToolDecision`
(the calls plus a `done` flag) instead of a bare list, so the loop ends on an explicit signal.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from retirementops.domain.routing import RiskFlag
from retirementops.domain.taxonomy import Intent

MAX_RAW_OUTPUT_BYTES = 64 * 1024
MAX_CALLS_PER_TURN = 6


# ---------------------------------------------------------------- errors
class ProviderError(Exception):
    """Base class. Messages are generic and never contain model output or prompt text."""


class ProviderOutputError(ProviderError):
    """The model's answer was not valid JSON for the expected schema."""


class ProviderTimeout(ProviderError):
    """The model did not answer within the per-call time limit."""


class ProviderUnavailable(ProviderError):
    """The model backend could not be reached or returned an error."""


# ---------------------------------------------------------------- answers (validated)
class _Answer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ClassificationResult(_Answer):
    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    risk_flags: list[RiskFlag] = Field(default_factory=list, max_length=len(RiskFlag))
    summary: str = Field(default="", max_length=300)


class ToolCall(_Answer):
    name: str = Field(pattern=r"^[a-z_]{1,64}$")
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolDecision(_Answer):
    tool_calls: list[ToolCall] = Field(default_factory=list, max_length=MAX_CALLS_PER_TURN)
    done: bool


class DraftResponse(_Answer):
    body: str = Field(min_length=1, max_length=4000)
    needs_human: bool
    reason: str = Field(default="", max_length=300)


def parse_answer[A: _Answer](raw: str, model: type[A]) -> A:
    """Parse raw model text into `model`. Any problem is a ProviderOutputError (no echo)."""
    if not isinstance(raw, str) or len(raw.encode()) > MAX_RAW_OUTPUT_BYTES:
        raise ProviderOutputError(f"{model.__name__}: output missing or too large")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ProviderOutputError(f"{model.__name__}: output is not valid JSON") from exc
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in e["loc"]) or "<root>" for e in exc.errors()})
        raise ProviderOutputError(
            f"{model.__name__}: schema mismatch at {', '.join(fields)}"
        ) from exc


# ---------------------------------------------------------------- requests (built by the host)
@dataclass(frozen=True, slots=True)
class SkillPrompt:
    name: str
    version: str
    instructions: str


@dataclass(frozen=True, slots=True)
class TicketView:
    """Ticket text for a prompt. The host masks it before building this (see prompts.py)."""

    ticket_id: str
    subject: str
    body: str


@dataclass(frozen=True, slots=True)
class ToolView:
    name: str
    description: str
    input_schema: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ToolResultView:
    name: str
    ok: bool
    content: str  # minimised JSON produced by the host


@dataclass(frozen=True, slots=True)
class ResolvedContext:
    """References the HOST resolved from the ticket and trusted data. The only ones allowed."""

    participant_refs: tuple[str, ...]
    plan_ids: tuple[str, ...]
    pay_period_ids: tuple[str, ...]
    pay_period_ends: tuple[str, ...]  # ISO dates, aligned with pay_period_ids


@dataclass(frozen=True, slots=True)
class ClassificationRequest:
    skill: SkillPrompt
    ticket: TicketView


@dataclass(frozen=True, slots=True)
class ToolDecisionRequest:
    skill: SkillPrompt
    ticket: TicketView
    resolved: ResolvedContext
    tools: tuple[ToolView, ...]
    results: tuple[ToolResultView, ...]
    calls_remaining: int


# Fixed vocabulary of preformatted facts the host may give the model for a draft.
FACT_KEYS = frozenset(
    {
        "participant_ref",
        "plan_id",
        "pay_period_id",
        "period_end",
        "finding_status",
        "eligible_pay",
        "deferral_pct",
        "employee_reported",
        "employee_expected",
        "employee_difference",
        "employer_reported",
        "employer_expected",
        "employer_difference",
    }
)


@dataclass(frozen=True, slots=True)
class DraftRequest:
    skill: SkillPrompt
    ticket: TicketView
    facts: Mapping[str, str] = field(default_factory=dict)
    routing_queue: str = ""

    def __post_init__(self) -> None:
        unknown = set(self.facts) - FACT_KEYS
        if unknown:
            raise ValueError("unknown fact keys")


# ---------------------------------------------------------------- the protocol
class ModelProvider(Protocol):
    @property
    def name(self) -> str: ...  # e.g. "ollama", "scripted"

    @property
    def model(self) -> str: ...  # e.g. "qwen3:8b"

    def model_digest(self) -> str | None: ...  # recorded on every run (T-19)

    def classify_ticket(self, request: ClassificationRequest) -> ClassificationResult: ...

    def choose_tools(self, request: ToolDecisionRequest) -> ToolDecision: ...

    def draft_response(self, request: DraftRequest) -> DraftResponse: ...
