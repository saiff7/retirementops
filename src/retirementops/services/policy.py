"""Service-ops policy retrieval: only versions in force today are ever returned."""

from __future__ import annotations

from retirementops.clock import Clock, utc_now
from retirementops.connectors.interfaces import PolicyKbReader
from retirementops.domain.authz import Scope
from retirementops.domain.records import PolicyCategory, PolicySnippet
from retirementops.services.context import RequestContext
from retirementops.services.errors import InvalidRequest

MAX_POLICY_RESULTS = 10


class PolicyService:
    def __init__(self, kb: PolicyKbReader, clock: Clock = utc_now) -> None:
        self._kb, self._clock = kb, clock

    def search(
        self,
        ctx: RequestContext,
        *,
        query: str | None,
        category: PolicyCategory | None,
        limit: int,
    ) -> list[PolicySnippet]:
        # Service-ops policies are internal guidance for service-ops readers (architecture.md §2).
        ctx.require(Scope.TICKETS_READ)
        if not 1 <= limit <= MAX_POLICY_RESULTS:
            raise InvalidRequest(f"limit must be 1..{MAX_POLICY_RESULTS}")
        return self._kb.search_snippets(
            query=query, category=category, on=self._clock().date(), limit=limit
        )
