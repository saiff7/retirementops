"""In-process tool-call metrics: counts by outcome and latency percentiles, bounded in memory.

Per process and reset on restart. Exported dashboards and alerts arrive in M8.
"""

from __future__ import annotations

import threading
from collections import defaultdict, deque
from typing import Any

_WINDOW = 1_000  # latency samples kept per tool


def _percentile(sorted_ms: list[float], pct: float) -> float:
    if not sorted_ms:
        return 0.0
    k = max(0, min(len(sorted_ms) - 1, round(pct / 100 * (len(sorted_ms) - 1))))
    return round(sorted_ms[k], 2)


class ToolMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._outcomes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._latency: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=_WINDOW))

    def record(self, tool: str, outcome: str, latency_ms: float) -> None:
        with self._lock:
            self._outcomes[tool][outcome] += 1
            self._latency[tool].append(latency_ms)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            out: dict[str, Any] = {}
            for tool, outcomes in sorted(self._outcomes.items()):
                lat = sorted(self._latency[tool])
                out[tool] = {
                    "calls": sum(outcomes.values()),
                    "outcomes": dict(sorted(outcomes.items())),
                    "p50_ms": _percentile(lat, 50),
                    "p95_ms": _percentile(lat, 95),
                }
            return out
