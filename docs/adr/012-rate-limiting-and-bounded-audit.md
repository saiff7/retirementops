# ADR-012: Rate limiting and bounded audit for anonymous traffic; pseudonymised audit records

**Status:** Accepted (2026-11-26). Added after the M1 review found an unbounded audit-write path.

## Context
The M1 review found two problems:
- Every unauthenticated 401 wrote one audit row, and audit writes are serialised. A flood of junk
  tokens could therefore grow the audit table and slow every request.
- Denial records stored the raw request path, which contains participant refs.

## Decision
1. **`RequestGuard`** (`services/request_guard.py`) runs in middleware **before routing,
   authentication or any database access**:
   - A per-client token bucket (default 300 requests/min, burst 60).
   - A per-client **failed-authentication** budget (default 10/min). Once it is spent, *every* request
     from that client gets `429 Retry-After` until it refills, including requests with valid tokens.
     Floods stop reaching token verification, the user directory and the audit log.
   - A per-principal budget for authenticated callers (default 600/min, burst 120).
2. **`AuditThrottle`** bounds audit writes from anonymous sources and for every `rate_limit.exceeded`
   event. Per 60-second window, it allows at most `anon_audit_per_client` rows per source (default 2)
   and `anon_audit_global` rows overall (default 60). Suppressed events are counted, and one
   `audit.anonymous_events_suppressed` row per window records how many there were and from how many
   sources. The audit trail stays honest that suppression happened, and anonymous audit growth is
   bounded by `anon_audit_global + 1` rows per window, whatever the request rate.
3. Authenticated denials (403/404) are always audited. Their volume is bounded by the per-principal
   budget.
4. **Pseudonymisation.** Audit records hold only HMAC pseudonyms, under a dedicated key
   `RO_AUDIT_PEPPER`, kept separate from the log key:
   - participant refs become `pref_…` and employer employee refs become `eref_…`;
   - client addresses become `client_…`;
   - SSN-like values are redacted;
   - request paths are stored as **route templates** (`/plans/{plan_id}/participants/{participant_ref}/…`),
     never as raw paths.

   `AuditLog.append` independently **refuses** any event that still contains a raw identifier, so a
   missed call site fails loudly instead of storing personal data. Client correlation IDs are accepted
   only as canonical UUIDs.
5. All limiter and throttle state is bounded in memory with LRU eviction (`max_tracked_keys`,
   default 10 000).

## Consequences and limits
- State is **per process**. With N workers, the effective limits are N× the configured values. A real
  deployment adds edge rate limiting (Cloudflare, M6) and, if needed, a shared store such as Redis or
  Durable Objects.
- Clients are identified by the TCP peer address. `X-Forwarded-For` is **not** trusted. Behind a proxy
  this must be replaced with the proxy's verified client-IP header, or every user shares one bucket.
- Users behind the same NAT as an attacker can be blocked while the attacker's failed-auth budget is
  spent. This is an accepted trade-off for the demo.
- LRU eviction lets an attacker with very many source addresses reset their own bucket. The global
  audit cap still bounds audit growth.
- Pseudonyms are keyed, not anonymous. Anyone holding `RO_AUDIT_PEPPER` can confirm whether a known ref
  appears, which is the intended investigation path.
