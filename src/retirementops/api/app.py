"""FastAPI application: read-only business API + dev-only mock login (M1).

No route in this module writes business data; `tests/security/test_api_security.py` fails if a
write route appears. Business writes arrive in M5, and only behind human approval.

Request order: correlation ID -> client rate limit / failed-auth block (no DB work) -> routing ->
authentication -> principal rate limit -> service. Denials are audited through RequestGuard, which
throttles anonymous events and stores route templates and pseudonyms, never raw identifiers.
"""

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import date
from typing import Annotated

from fastapi import Depends, FastAPI, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from retirementops import __version__
from retirementops.api import schemas as s
from retirementops.domain.records import ContributionType
from retirementops.domain.taxonomy import TicketStatus
from retirementops.governance.logging import get_logger
from retirementops.services.container import Container
from retirementops.services.context import RequestContext
from retirementops.services.errors import (
    Forbidden,
    InvalidRequest,
    NotFound,
    RateLimited,
    ServiceError,
    Unauthenticated,
)

TicketId = Annotated[str, Path(pattern=r"^ZD-\d{4,6}$")]
PlanId = Annotated[str, Path(pattern=r"^PLAN-\d{3}$")]
ParticipantRef = Annotated[str, Path(pattern=r"^PART-\d{4}$")]
PayPeriodId = Annotated[str, Query(pattern=r"^PP-\d{4}-\d{2}$")]
_STATUS = {
    Unauthenticated: 401,
    Forbidden: 403,
    NotFound: 404,
    InvalidRequest: 400,
    RateLimited: 429,
}
_AUDIT_EVENT = {
    Unauthenticated: "auth.failed",
    Forbidden: "access.denied",
    NotFound: "access.not_found",
    RateLimited: "rate_limit.exceeded",
}

log = get_logger("retirementops.api")


def _correlation_id(supplied: str) -> str:
    """Accept only a canonical UUID from the client; anything else is replaced (no free text)."""
    try:
        return str(uuid.UUID(supplied)) if len(supplied) == 36 else str(uuid.uuid4())
    except ValueError:
        return str(uuid.uuid4())


def _route_template(request: Request) -> str:
    """The matched route pattern (e.g. /plans/{plan_id}/...), never the raw path with identifiers."""
    route = request.scope.get("route")
    return str(getattr(route, "path", "<unmatched>"))


def _error(status: int, exc: ServiceError, cid: str) -> JSONResponse:
    headers: dict[str, str] = {}
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    if isinstance(exc, RateLimited):
        headers["Retry-After"] = str(exc.retry_after)
    body = s.ErrorOut(error=exc.code, message=str(exc), correlation_id=cid)
    return JSONResponse(body.model_dump(), status_code=status, headers=headers or None)


def create_app(container: Container, *, dispose_on_shutdown: bool = False) -> FastAPI:
    guard = container.guard

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        if dispose_on_shutdown:
            container.dispose()

    app = FastAPI(
        title="RetirementOps API (synthetic-data prototype)",
        version=__version__,
        description="SYNTHETIC DATA ONLY. Mock identity, not production SSO. Read-only in M1.",
        lifespan=lifespan,
    )

    def audit_denial(request: Request, exc: ServiceError) -> None:
        event = next((e for cls, e in _AUDIT_EVENT.items() if isinstance(exc, cls)), None)
        if event is None:
            return
        details: dict[str, object] = {"route": _route_template(request), "method": request.method}
        target = None
        if isinstance(exc, Forbidden):
            details["policy_reason"] = exc.reason
        if isinstance(exc, NotFound):
            details["resource"], target = exc.resource, exc.ref  # ref is pseudonymised on write
        if isinstance(exc, Unauthenticated):
            details["reason"] = str(exc)
        guard.audit(
            getattr(request.state, "ctx", None),
            event,
            client=request.state.client,
            correlation_id=request.state.correlation_id,
            target_ref=target,
            details=details,
        )

    @app.middleware("http")
    async def guard_and_access_log(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        cid = _correlation_id(request.headers.get("x-correlation-id", ""))
        request.state.correlation_id = cid
        request.state.principal_id = None
        request.state.client = guard.client_key(request.client.host if request.client else None)
        started = time.perf_counter()
        try:
            guard.check_client(request.state.client)  # before routing, auth or any DB access
        except RateLimited as exc:
            audit_denial(request, exc)
            response: Response = _error(429, exc, cid)
        else:
            response = await call_next(request)
        response.headers["X-Correlation-ID"] = cid
        log.info(
            "http_request",
            correlation_id=cid,
            method=request.method,
            route=_route_template(request),
            status=response.status_code,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            principal=request.state.principal_id,
            client=request.state.client,
        )
        return response

    def ctx_dep(request: Request) -> RequestContext:
        header = request.headers.get("authorization", "")
        token = header[7:].strip() if header[:7].lower() == "bearer " else None
        principal = container.identity.authenticate(token)
        request.state.principal_id = principal.principal_id
        ctx = RequestContext(principal, str(request.state.correlation_id))
        request.state.ctx = ctx  # set first, so a rate-limit denial is attributed to the principal
        guard.check_principal(principal.principal_id)
        return ctx

    Ctx = Annotated[RequestContext, Depends(ctx_dep)]

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, exc: ServiceError) -> JSONResponse:
        status = next((code for cls, code in _STATUS.items() if isinstance(exc, cls)), 500)
        if isinstance(exc, Unauthenticated):
            guard.note_auth_failure(request.state.client)
        audit_denial(request, exc)
        return _error(status, exc, request.state.correlation_id)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Report locations only; never echo submitted values.
        where = sorted({".".join(str(p) for p in e.get("loc", ())) for e in exc.errors()})
        body = s.ErrorOut(
            error="invalid_request",
            message="invalid: " + ", ".join(where),
            correlation_id=request.state.correlation_id,
        )
        return JSONResponse(body.model_dump(), status_code=422)

    # ------------------------------------------------------------------ routes
    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__, "data": "synthetic"}

    @app.post(
        "/auth/dev-token",
        response_model=s.DevTokenOut,
        summary="DEV ONLY: mock login for a seeded fictional user",
    )
    def dev_token(req: s.DevTokenRequest) -> s.DevTokenOut:
        return s.DevTokenOut(access_token=container.identity.issue_dev_token(req.user_id))

    @app.get("/tickets", response_model=s.TicketPage)
    def search_tickets(
        ctx: Ctx,
        q: Annotated[str | None, Query(max_length=200)] = None,
        status: TicketStatus | None = None,
        priority: Annotated[str | None, Query(pattern=r"^(low|normal|high|urgent)$")] = None,
        page_size: Annotated[int, Query(ge=1, le=25)] = 10,
    ) -> s.TicketPage:
        items = container.tickets.search(
            ctx, query=q, status=status, priority=priority, page_size=page_size
        )
        return s.TicketPage(items=[s.TicketSummaryOut.of(t) for t in items], page_size=page_size)

    @app.get("/tickets/{ticket_id}", response_model=s.TicketOut)
    def get_ticket(ctx: Ctx, ticket_id: TicketId) -> s.TicketOut:
        return s.TicketOut.of(container.tickets.get(ctx, ticket_id))

    @app.get("/plans/{plan_id}", response_model=s.PlanOut)
    def get_plan(ctx: Ctx, plan_id: PlanId) -> s.PlanOut:
        return s.PlanOut.of(container.plans.get_plan(ctx, plan_id))

    @app.get("/plans/{plan_id}/rules/applicable", response_model=s.RuleSelectionOut)
    def applicable_rule(
        ctx: Ctx, plan_id: PlanId, effective_date: date, contribution_type: ContributionType
    ) -> s.RuleSelectionOut:
        sel = container.plans.get_applicable_rule(
            ctx, plan_id, effective_date=effective_date, contribution_type=contribution_type
        )
        return s.RuleSelectionOut.of(sel)

    @app.get(
        "/plans/{plan_id}/participants/{participant_ref}/enrollment",
        response_model=list[s.EnrollmentOut],
    )
    def enrollment(
        ctx: Ctx, plan_id: PlanId, participant_ref: ParticipantRef
    ) -> list[s.EnrollmentOut]:
        return [
            s.EnrollmentOut.of(e)
            for e in container.plans.get_enrollments(ctx, plan_id, participant_ref)
        ]

    @app.get(
        "/plans/{plan_id}/participants/{participant_ref}/contributions",
        response_model=s.ContributionRowsOut,
    )
    def contributions(
        ctx: Ctx, plan_id: PlanId, participant_ref: ParticipantRef, pay_period: PayPeriodId
    ) -> s.ContributionRowsOut:
        res = container.contributions.list_rows(ctx, plan_id, participant_ref, pay_period)
        return s.ContributionRowsOut(
            participant_ref=participant_ref,
            plan_id=plan_id,
            pay_period=s.PayPeriodOut.of(res.pay_period),
            rows=[s.PayrollRowOut.of(r) for r in res.rows],
        )

    @app.get(
        "/plans/{plan_id}/participants/{participant_ref}/contribution-finding",
        response_model=s.FindingOut,
    )
    def contribution_finding(
        ctx: Ctx, plan_id: PlanId, participant_ref: ParticipantRef, pay_period: PayPeriodId
    ) -> s.FindingOut:
        return s.FindingOut.of(
            container.contributions.get_finding(ctx, plan_id, participant_ref, pay_period)
        )

    return app
