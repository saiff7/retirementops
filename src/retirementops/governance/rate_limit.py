"""In-process rate limiting and audit-write throttling (ADR-012).

Both structures are bounded in memory (LRU eviction at `max_keys`) so a flood of distinct keys cannot
exhaust the process. State is per process: with several workers, each enforces its own limits. A real
deployment adds edge rate limiting (Cloudflare) and a shared store; see ADR-012.
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

MonotonicClock = Callable[[], float]


class TokenBucketLimiter:
    """Classic token bucket per key: `capacity` burst, refilled at `refill_per_second`."""

    def __init__(
        self,
        *,
        capacity: int,
        refill_per_second: float,
        max_keys: int = 10_000,
        clock: MonotonicClock = time.monotonic,
    ) -> None:
        if capacity < 1 or refill_per_second <= 0 or max_keys < 1:
            raise ValueError("invalid limiter configuration")
        self._capacity, self._rate, self._max_keys = float(capacity), refill_per_second, max_keys
        self._clock = clock
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()  # key -> (tokens, at)
        self._lock = threading.Lock()

    def _refill(self, key: str) -> float:
        now = self._clock()
        tokens, at = self._buckets.get(key, (self._capacity, now))
        tokens = min(self._capacity, tokens + (now - at) * self._rate)
        self._buckets[key] = (tokens, now)
        self._buckets.move_to_end(key)
        while len(self._buckets) > self._max_keys:
            self._buckets.popitem(last=False)
        return tokens

    def allow(self, key: str, cost: float = 1.0) -> bool:
        """Consume `cost` tokens if available."""
        with self._lock:
            tokens = self._refill(key)
            if tokens < cost:
                return False
            self._buckets[key] = (tokens - cost, self._buckets[key][1])
            return True

    def has_capacity(self, key: str, cost: float = 1.0) -> bool:
        """Check without consuming (used to block clients whose failure budget is spent)."""
        with self._lock:
            return self._refill(key) >= cost

    def retry_after(self, key: str, cost: float = 1.0) -> int:
        with self._lock:
            missing = max(0.0, cost - self._refill(key))
        return max(1, math.ceil(missing / self._rate))

    def __len__(self) -> int:
        return len(self._buckets)


@dataclass(frozen=True, slots=True)
class ThrottleDecision:
    record: bool
    # Set at most once per window: counts of events suppressed in the window that just closed.
    summary: dict[str, int] | None


class AuditThrottle:
    """Bounds audit writes for noisy event sources (anonymous traffic).

    Per fixed window: at most `per_key` rows per key and `global_limit` rows overall. Suppressed events
    are counted, and the first event of the next window carries a summary so the audit trail still
    records that suppression happened (one extra row per window at most).
    """

    def __init__(
        self,
        *,
        window_seconds: float,
        per_key: int,
        global_limit: int,
        max_keys: int = 10_000,
        clock: MonotonicClock = time.monotonic,
    ) -> None:
        if window_seconds <= 0 or per_key < 1 or global_limit < 1:
            raise ValueError("invalid throttle configuration")
        self._window, self._per_key, self._global = window_seconds, per_key, global_limit
        self._max_keys, self._clock = max_keys, clock
        self._lock = threading.Lock()
        self._window_start = clock()
        self._counts: OrderedDict[str, int] = OrderedDict()
        self._recorded = 0
        self._suppressed = 0
        self._suppressed_keys: set[str] = set()

    def admit(self, key: str) -> ThrottleDecision:
        with self._lock:
            summary = self._roll_window()
            n = self._counts.get(key, 0)
            if n >= self._per_key or self._recorded >= self._global:
                self._suppressed += 1
                if len(self._suppressed_keys) < self._max_keys:
                    self._suppressed_keys.add(key)
                return ThrottleDecision(False, summary)
            self._counts[key] = n + 1
            self._counts.move_to_end(key)
            while len(self._counts) > self._max_keys:
                self._counts.popitem(last=False)
            self._recorded += 1
            return ThrottleDecision(True, summary)

    def _roll_window(self) -> dict[str, int] | None:
        now = self._clock()
        if now - self._window_start < self._window:
            return None
        summary = None
        if self._suppressed:
            summary = {
                "suppressed_events": self._suppressed,
                "distinct_sources": len(self._suppressed_keys),
            }
        self._window_start = now
        self._counts.clear()
        self._recorded = 0
        self._suppressed = 0
        self._suppressed_keys = set()
        return summary
