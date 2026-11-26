"""Streamable HTTP transport for the Service Operations MCP server (stateless, JSON responses).

Before any MCP processing, `McpGate` applies the per-client rate limit and the failed-auth block,
verifies the bearer token (mock identity in M1/M2, OAuth at the Cloudflare edge in M6), and attaches
the verified principal to the request. Unauthenticated requests never reach the MCP session.
The SDK's DNS-rebinding protection is on, and binding is limited to loopback (see __main__).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from retirementops.domain.authz import Principal
from retirementops.domain.authz import Scope as AuthScope
from retirementops.mcp_servers.service_ops.server import HttpPrincipalSource, ServiceOpsMcp
from retirementops.services.container import Container
from retirementops.services.errors import RateLimited, Unauthenticated

REALM = 'Bearer realm="retirementops-mcp"'


def _correlation_id(supplied: str) -> str:
    try:
        return str(uuid.UUID(supplied)) if len(supplied) == 36 else str(uuid.uuid4())
    except ValueError:
        return str(uuid.uuid4())


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    return header[7:].strip() if header[:7].lower() == "bearer " else None


async def _authenticate(
    container: Container, request: Request, client: str, cid: str
) -> Principal | Response:
    try:
        return await anyio.to_thread.run_sync(container.identity.authenticate, _bearer(request))
    except Unauthenticated as exc:
        reason = str(exc)
        container.guard.note_auth_failure(client)
        await anyio.to_thread.run_sync(
            lambda: container.guard.audit(
                None,
                "auth.failed",
                client=client,
                correlation_id=cid,
                details={"route": request.url.path, "method": request.method, "reason": reason},
            )
        )
        return JSONResponse(
            {"error": "unauthenticated", "correlation_id": cid},
            status_code=401,
            headers={"WWW-Authenticate": REALM},
        )


async def _client_gate(container: Container, request: Request) -> tuple[str, str, Response | None]:
    cid = _correlation_id(request.headers.get("x-correlation-id", ""))
    client = container.guard.client_key(request.client.host if request.client else None)
    try:
        container.guard.check_client(client)
    except RateLimited as exc:
        retry_after = exc.retry_after
        await anyio.to_thread.run_sync(
            lambda: container.guard.audit(
                None,
                "rate_limit.exceeded",
                client=client,
                correlation_id=cid,
                details={"route": request.url.path, "method": request.method},
            )
        )
        return (
            cid,
            client,
            JSONResponse(
                {"error": "rate_limited", "correlation_id": cid},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            ),
        )
    return cid, client, None


class McpGate:
    """ASGI app (a class, so Starlette routes the raw scope to it): gate, then hand to the SDK."""

    def __init__(self, container: Container, manager: StreamableHTTPSessionManager) -> None:
        self._container, self._manager = container, manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive)
        cid, client, blocked = await _client_gate(self._container, request)
        if blocked is not None:
            await blocked(scope, receive, send)
            return
        principal = await _authenticate(self._container, request, client, cid)
        if isinstance(principal, Response):
            await principal(scope, receive, send)
            return
        state: dict[str, Any] = scope.setdefault("state", {})
        state.update(principal=principal, correlation_id=cid, client=client)
        await self._manager.handle_request(scope, receive, send)


def allowed_hosts(port: int) -> list[str]:
    """Host headers accepted by DNS-rebinding protection: every loopback bind __main__ allows."""
    return [f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"]


def create_http_app(
    container: Container, *, port: int, dispose_on_shutdown: bool = False
) -> Starlette:
    mcp = ServiceOpsMcp(container, HttpPrincipalSource())
    hosts = allowed_hosts(port)
    manager = StreamableHTTPSessionManager(
        app=mcp.server,
        stateless=True,  # each request self-contained; state lives in Postgres (spec §6)
        json_response=True,
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts,
            allowed_origins=[f"http://{h}" for h in hosts],
        ),
        max_request_body_size=256 * 1024,
    )

    async def health(_request: Request) -> Response:
        return JSONResponse(
            {"status": "ok", "server": "retirementops-service-ops", "data": "synthetic"}
        )

    async def metrics(request: Request) -> Response:
        cid, client, blocked = await _client_gate(container, request)
        if blocked is not None:
            return blocked
        principal = await _authenticate(container, request, client, cid)
        if isinstance(principal, Response):
            return principal
        if not principal.has_scope(AuthScope.ADMIN_OPS):
            return JSONResponse({"error": "forbidden", "correlation_id": cid}, status_code=403)
        return Response(
            json.dumps(mcp.metrics.snapshot(), sort_keys=True), media_type="application/json"
        )

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with manager.run():
            yield
        if dispose_on_shutdown:
            container.dispose()

    app = Starlette(
        routes=[
            Route("/mcp", endpoint=McpGate(container, manager), methods=["GET", "POST", "DELETE"]),
            Route("/health", health, methods=["GET"]),
            Route("/metrics", metrics, methods=["GET"]),
        ],
        lifespan=lifespan,
    )
    app.state.mcp = mcp
    return app
