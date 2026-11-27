"""ScriptedProvider: deterministic, offline model stand-in for tests and CI (ADR-005, ADR-010).

A script supplies each answer as a validated object, as raw text (parsed exactly like real model
output, so malformed JSON is exercised), as a function of the request, or as an exception to raise
(timeouts, outages). The canned scripts in `scripts.py` include adversarial ones; they prove the
host enforces safety whatever the "model" does.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from retirementops.agent_runtime.providers.contracts import (
    ClassificationRequest,
    ClassificationResult,
    DraftRequest,
    DraftResponse,
    ToolDecision,
    ToolDecisionRequest,
    _Answer,
    parse_answer,
)

Step = object  # an answer object, raw str, Exception, or Callable[[request], answer | str]


@dataclass
class Script:
    name: str
    classification: Step
    tool_turns: list[Step] = field(default_factory=list)
    draft: Step = None


class ScriptedProvider:
    def __init__(self, script: Script) -> None:
        self._script = script
        self._turn = 0
        self.requests: list[object] = []  # what the host sent, for test assertions

    @property
    def name(self) -> str:
        return "scripted"

    @property
    def model(self) -> str:
        return f"scripted-{self._script.name}"

    def model_digest(self) -> str | None:
        return None

    def _answer[A: _Answer](self, step: Step, request: object, model: type[A]) -> A:
        self.requests.append(request)
        if isinstance(step, BaseException):
            raise step
        if callable(step) and not isinstance(step, _Answer):
            step = step(request)
        if isinstance(step, str):
            return parse_answer(step, model)
        if isinstance(step, model):
            return step
        raise TypeError("script step has the wrong type")

    def classify_ticket(self, request: ClassificationRequest) -> ClassificationResult:
        return self._answer(self._script.classification, request, ClassificationResult)

    def choose_tools(self, request: ToolDecisionRequest) -> ToolDecision:
        turns = self._script.tool_turns
        step: Step = turns[self._turn] if self._turn < len(turns) else ToolDecision(done=True)
        self._turn += 1
        return self._answer(step, request, ToolDecision)

    def draft_response(self, request: DraftRequest) -> DraftResponse:
        if self._script.draft is None:
            raise TypeError("script has no draft step")
        return self._answer(self._script.draft, request, DraftResponse)
