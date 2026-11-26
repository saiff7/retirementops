"""Per-request context passed to every service call."""

from __future__ import annotations

from dataclasses import dataclass

from retirementops.domain.authz import Principal, Scope
from retirementops.services.errors import Forbidden


@dataclass(frozen=True, slots=True)
class RequestContext:
    principal: Principal
    correlation_id: str

    def require(self, *scopes: Scope) -> None:
        missing = [s.value for s in scopes if not self.principal.has_scope(s)]
        if missing:
            raise Forbidden(f"missing scope: {', '.join(missing)}")
