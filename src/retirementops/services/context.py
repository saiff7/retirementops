"""Per-request context passed to every service call."""

from __future__ import annotations

from dataclasses import dataclass

from retirementops.domain.authz import Principal, Scope
from retirementops.domain.taxonomy import Queue
from retirementops.services.errors import Forbidden, NotFound


@dataclass(frozen=True, slots=True)
class RequestContext:
    principal: Principal
    correlation_id: str

    def require(self, *scopes: Scope) -> None:
        missing = [s.value for s in scopes if not self.principal.has_scope(s)]
        if missing:
            raise Forbidden(f"missing scope: {', '.join(missing)}")

    # ------------------------------------------------------------ agent run binding (M3)
    # For agent run tokens, anything outside the host-resolved binding is "not found", exactly like
    # another tenant's object, so a model-supplied reference can neither read nor probe (T-03, T-04).
    # Human principals have no binding and are unaffected.

    def bound_ticket(self, ticket_id: str) -> None:
        run = self.principal.run
        if run is not None and ticket_id != run.ticket_id:
            raise NotFound("ticket", ticket_id)

    def bound_plan(self, plan_id: str) -> None:
        run = self.principal.run
        if run is not None and plan_id not in run.plan_ids:
            raise NotFound("plan", plan_id)

    def bound_participant(self, participant_ref: str) -> None:
        run = self.principal.run
        if run is not None and participant_ref not in run.participant_refs:
            raise NotFound("participant", participant_ref)

    def bound_pay_period(self, pay_period_id: str) -> None:
        run = self.principal.run
        if run is not None and pay_period_id not in run.pay_period_ids:
            raise NotFound("pay_period", pay_period_id)

    def not_in_run(self, what: str) -> None:
        if self.principal.run is not None:
            raise Forbidden(f"{what} is not available to agent runs")

    def permitted_queue(self, queue: Queue) -> None:
        """ADR-013: an agent may propose only the queue the routing rules engine chose."""
        run = self.principal.run
        if run is not None and queue is not run.permitted_queue:
            raise Forbidden("target queue is not the one the routing rules permit for this run")
