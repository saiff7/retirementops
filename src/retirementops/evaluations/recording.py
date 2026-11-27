"""A recording wrapper around any ModelProvider (ADR-010 Amendment 1, point 4).

The orchestrator is not changed for evaluation. This wrapper sits between it and the real provider
and records, per model call:
- the kind of call and its latency;
- whether the answer parsed (parse failures are recorded even when the orchestrator retries them
  silently);
- the parsed classification, and every tool decision with the arguments the model proposed (the
  run trace keeps only argument hashes);
- whether the request already carried the host's completeness feedback, so first-pass choices can
  be scored separately from host-assisted ones.

Draft text is kept in memory for scoring only; reports never contain it (point 12).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from retirementops.agent_runtime.providers.contracts import (
    ClassificationRequest,
    ClassificationResult,
    DraftRequest,
    DraftResponse,
    ModelProvider,
    ProviderError,
    ProviderOutputError,
    ToolDecision,
    ToolDecisionRequest,
)


@dataclass
class ModelCall:
    kind: str  # classify | choose_tools | draft
    ok: bool
    latency_ms: float
    error: str | None = None  # exception class name, never its message
    host_feedback_seen: bool = False


@dataclass
class Recording:
    calls: list[ModelCall] = field(default_factory=list)
    classification: ClassificationResult | None = None
    tool_turns: list[list[dict[str, Any]]] = field(default_factory=list)
    tool_turn_feedback: list[bool] = field(default_factory=list)
    draft: DraftResponse | None = None

    @property
    def parse_failures(self) -> int:
        return sum(1 for c in self.calls if c.error == ProviderOutputError.__name__)

    @property
    def first_pass_calls(self) -> list[dict[str, Any]]:
        """Every tool call the model chose before any host completeness feedback."""
        out: list[dict[str, Any]] = []
        for turn, feedback in zip(self.tool_turns, self.tool_turn_feedback, strict=True):
            if feedback:
                break
            out.extend(turn)
        return out

    @property
    def all_calls(self) -> list[dict[str, Any]]:
        return [c for turn in self.tool_turns for c in turn]


class RecordingProvider:
    """Implements ModelProvider by delegation; records everything listed in the module doc."""

    def __init__(self, inner: ModelProvider, clock: Any = time.perf_counter) -> None:
        self._inner, self._clock = inner, clock
        self.recording = Recording()

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def model(self) -> str:
        return self._inner.model

    def model_digest(self) -> str | None:
        return self._inner.model_digest()

    def _timed[T](self, kind: str, fn: Callable[[], T], feedback: bool = False) -> T:
        start = self._clock()
        try:
            result = fn()
        except ProviderError as exc:
            self.recording.calls.append(
                ModelCall(kind, False, (self._clock() - start) * 1000, type(exc).__name__, feedback)
            )
            raise
        self.recording.calls.append(
            ModelCall(kind, True, (self._clock() - start) * 1000, None, feedback)
        )
        return result

    def classify_ticket(self, request: ClassificationRequest) -> ClassificationResult:
        result: ClassificationResult = self._timed(
            "classify", lambda: self._inner.classify_ticket(request)
        )
        self.recording.classification = result
        return result

    def choose_tools(self, request: ToolDecisionRequest) -> ToolDecision:
        feedback = any(r.name == "host_evidence_check" for r in request.results)
        decision: ToolDecision = self._timed(
            "choose_tools", lambda: self._inner.choose_tools(request), feedback
        )
        self.recording.tool_turns.append(
            [{"name": c.name, "arguments": dict(c.arguments)} for c in decision.tool_calls]
        )
        self.recording.tool_turn_feedback.append(feedback)
        return decision

    def draft_response(self, request: DraftRequest) -> DraftResponse:
        result: DraftResponse = self._timed("draft", lambda: self._inner.draft_response(request))
        self.recording.draft = result
        return result
