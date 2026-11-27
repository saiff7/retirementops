"""The worker's tool bridge: calls the REAL Service Ops MCP server in process, with the run token.

Nothing is short-circuited: every call goes through the MCP SDK client, the server's principal
resolution (the run token is re-verified on each call), scope checks, argument validation, the run
binding in the services, RLS and the audit log. The host sees only structured results.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import anyio
from mcp.client.client import Client

from retirementops.domain.evidence import content_hash
from retirementops.mcp_servers.service_ops.server import ServiceOpsMcp, TokenPrincipalSource
from retirementops.services.container import Container


@dataclass(frozen=True, slots=True)
class ToolResult:
    name: str
    ok: bool
    error: str | None  # the server's error code, e.g. "not_found", "forbidden"
    data: dict[str, Any]
    latency_ms: float
    result_sha256: str


class McpToolClient:
    def __init__(self, container: Container, run_token: str) -> None:
        self._server = ServiceOpsMcp(
            container, TokenPrincipalSource(container, run_token, client="agent-runtime")
        )

    def call(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        started = time.perf_counter()

        async def go() -> tuple[bool, dict[str, Any]]:
            async with Client(self._server.server) as client:
                r = await client.call_tool(name, arguments)
                return bool(r.is_error), dict(r.structured_content or {})

        is_error, data = anyio.run(go)
        latency = round((time.perf_counter() - started) * 1000, 2)
        error = str(data.get("error", "error")) if is_error else None
        return ToolResult(name, not is_error, error, data, latency, content_hash(data))
