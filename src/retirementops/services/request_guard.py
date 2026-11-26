"""Abuse protection in front of authentication and audit (ADR-012).

Three controls, all checked before any database work for the request:
1. Per-client request budget (token bucket keyed by a pseudonymised client address).
2. Per-client failed-authentication budget. When it is spent, the client gets 429 for every request
   until it refills, so a credential-stuffing or garbage-token flood stops reaching token checks,
   the user directory and the audit log.
3. Per-principal request budget for authenticated callers.

Audit writes for anonymous events (and all rate-limit events) go through an AuditThrottle: at most `anon_audit_per_client` rows per
client and `anon_audit_global` rows overall per window, plus one summary row per window that records
how many events were suppressed. Anonymous traffic therefore cannot grow the audit table faster than a
fixed bound, whatever the request rate.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from retirementops.governance.rate_limit import AuditThrottle, MonotonicClock, TokenBucketLimiter
from retirementops.services.audit_service import AuditService
from retirementops.services.context import RequestContext
from retirementops.services.errors import RateLimited


@dataclass(frozen=True, slots=True)
class GuardConfig:
    client_requests_per_minute: int = 300
    client_burst: int = 60
    auth_failures_per_minute: int = 10
    principal_requests_per_minute: int = 600
    principal_burst: int = 120
    audit_window_seconds: float = 60.0
    anon_audit_per_client: int = 2
    anon_audit_global: int = 60
    max_tracked_keys: int = 10_000


class RequestGuard:
    def __init__(
        self, config: GuardConfig, audit: AuditService, clock: MonotonicClock = time.monotonic
    ) -> None:
        self.config = config
        self._audit = audit
        k = config.max_tracked_keys
        self._clients = TokenBucketLimiter(
            capacity=config.client_burst,
            refill_per_second=config.client_requests_per_minute / 60,
            max_keys=k,
            clock=clock,
        )
        self._auth_failures = TokenBucketLimiter(
            capacity=config.auth_failures_per_minute,
            refill_per_second=config.auth_failures_per_minute / 60,
            max_keys=k,
            clock=clock,
        )
        self._principals = TokenBucketLimiter(
            capacity=config.principal_burst,
            refill_per_second=config.principal_requests_per_minute / 60,
            max_keys=k,
            clock=clock,
        )
        self._anon_audit = AuditThrottle(
            window_seconds=config.audit_window_seconds,
            per_key=config.anon_audit_per_client,
            global_limit=config.anon_audit_global,
            max_keys=k,
            clock=clock,
        )

    def client_key(self, host: str | None) -> str:
        """Pseudonymised client address. Raw IPs are never stored. X-Forwarded-For is NOT trusted."""
        return self._audit.pseudonym("client", host or "unknown")

    def check_client(self, client: str) -> None:
        """Raises RateLimited if the client's request or failed-auth budget is spent."""
        if not self._auth_failures.has_capacity(client):
            raise RateLimited(self._auth_failures.retry_after(client))
        if not self._clients.allow(client):
            raise RateLimited(self._clients.retry_after(client))

    def check_principal(self, principal_id: str) -> None:
        if not self._principals.allow(principal_id):
            raise RateLimited(self._principals.retry_after(principal_id))

    def note_auth_failure(self, client: str) -> None:
        self._auth_failures.allow(client)  # spends one token; empty bucket -> blocked

    def audit(
        self,
        ctx: RequestContext | None,
        event_type: str,
        *,
        client: str,
        correlation_id: str,
        target_ref: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> bool:
        """Record an audit event. Anonymous events are throttled; returns whether a row was written.

        Authenticated denials are always written (their volume is bounded by the principal budget);
        anonymous events and rate-limit events are throttled.
        """
        # Rate-limit events are throttled even for authenticated callers: a principal hammering past
        # its budget must not turn every 429 into an audit write.
        if ctx is not None and event_type != "rate_limit.exceeded":
            self._audit.record(ctx, event_type, target_ref=target_ref, details=details)
            return True
        key = client if ctx is None else f"principal:{ctx.principal.principal_id}"
        decision = self._anon_audit.admit(key)
        if decision.summary:
            self._audit.record(
                None,
                "audit.anonymous_events_suppressed",
                actor_override="system",
                anonymous_correlation_id=correlation_id,
                details=decision.summary,
            )
        if not decision.record:
            return False
        self._audit.record(
            ctx,
            event_type,
            target_ref=target_ref,
            anonymous_correlation_id=correlation_id,
            details={**(details or {}), "client": client},
        )
        return True
