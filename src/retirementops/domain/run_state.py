"""The agent-run state machine (architecture.md §3). Pure. Any transition not listed is refused.

RECEIVED -> CLASSIFIED -> RESOLVED -> EVIDENCE_GATHERED -> DRAFTED -> PENDING_REVIEW
every non-terminal state -> NEEDS_MANUAL_REVIEW (failure, timeout, budget, malformed output)
"""

from __future__ import annotations

from enum import StrEnum


class RunState(StrEnum):
    RECEIVED = "RECEIVED"
    CLASSIFIED = "CLASSIFIED"
    RESOLVED = "RESOLVED"
    EVIDENCE_GATHERED = "EVIDENCE_GATHERED"
    DRAFTED = "DRAFTED"
    PENDING_REVIEW = "PENDING_REVIEW"
    NEEDS_MANUAL_REVIEW = "NEEDS_MANUAL_REVIEW"


TERMINAL = frozenset({RunState.PENDING_REVIEW, RunState.NEEDS_MANUAL_REVIEW})
_FORWARD: dict[RunState, RunState] = {
    RunState.RECEIVED: RunState.CLASSIFIED,
    RunState.CLASSIFIED: RunState.RESOLVED,
    RunState.RESOLVED: RunState.EVIDENCE_GATHERED,
    RunState.EVIDENCE_GATHERED: RunState.DRAFTED,
    RunState.DRAFTED: RunState.PENDING_REVIEW,
}
ALLOWED: frozenset[tuple[RunState, RunState]] = frozenset(
    set(_FORWARD.items())
    | {(s, RunState.NEEDS_MANUAL_REVIEW) for s in RunState if s not in TERMINAL}
)


class IllegalTransition(Exception):
    pass


class RunStateMachine:
    """Tracks one run's path. Terminal states are final."""

    def __init__(self) -> None:
        self._path: list[RunState] = [RunState.RECEIVED]

    @property
    def state(self) -> RunState:
        return self._path[-1]

    @property
    def path(self) -> tuple[RunState, ...]:
        return tuple(self._path)

    def advance(self, to: RunState) -> None:
        if (self.state, to) not in ALLOWED:
            raise IllegalTransition(f"{self.state.value} -> {to.value} is not allowed")
        self._path.append(to)

    def fail(self) -> None:
        """Fail closed to manual review from any non-terminal state; a no-op once terminal."""
        if self.state not in TERMINAL:
            self.advance(RunState.NEEDS_MANUAL_REVIEW)
