"""Service-layer errors. Messages are safe to return to callers: no values, no internals."""

from __future__ import annotations


class ServiceError(Exception):
    code = "error"


class Unauthenticated(ServiceError):
    code = "unauthenticated"


class Forbidden(ServiceError):
    """The principal lacks a required scope or role. Returned as 403."""

    code = "forbidden"

    def __init__(self, reason: str, *, target: str | None = None) -> None:
        super().__init__(reason)
        self.reason, self.target = reason, target


class NotFound(ServiceError):
    """Object absent OR outside the principal's tenants (deliberately indistinguishable). 404."""

    code = "not_found"

    def __init__(self, resource: str, ref: str) -> None:
        super().__init__(f"{resource} not found")
        self.resource, self.ref = resource, ref


class InvalidRequest(ServiceError):
    code = "invalid_request"


class RateLimited(ServiceError):
    """Too many requests or too many failed authentications from one source. Returned as 429."""

    code = "rate_limited"

    def __init__(self, retry_after: int) -> None:
        super().__init__("too many requests; retry later")
        self.retry_after = retry_after
