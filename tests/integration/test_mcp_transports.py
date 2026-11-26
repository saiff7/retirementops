"""Real transports: Streamable HTTP via uvicorn, and stdio via a subprocess, using SDK clients."""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import anyio
import httpx2
import pytest
import uvicorn
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from retirementops.config import ROOT, Settings
from retirementops.governance.tokens import TokenService
from retirementops.mcp_servers.service_ops import __main__ as cli
from retirementops.mcp_servers.service_ops.http import create_http_app
from retirementops.seed.generator import Dataset
from retirementops.services.container import build_container
from tests.conftest import GENEROUS_GUARD

pytestmark = pytest.mark.integration
Bearer = Callable[[str], dict[str, str]]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def http_server(
    settings: Settings, test_db: str, dataset: Dataset, token_service: TokenService
) -> Iterator[str]:
    port = _free_port()
    container = build_container(
        settings, db_name=test_db, tokens=token_service, guard_config=GENEROUS_GUARD
    )
    app = create_http_app(container, port=port)
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="warning", access_log=False, lifespan="on"
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn did not start"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)
    container.dispose()


async def _http_session(base: str, headers: dict[str, str], fn: Any) -> Any:
    async with (
        httpx2.AsyncClient(headers=headers, timeout=10) as hc,
        streamable_http_client(f"{base}/mcp", http_client=hc) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        return await fn(session)


def test_http_discovery_and_call(http_server: str, bearer: Bearer) -> None:
    async def fn(s: ClientSession) -> tuple[list[str], Any]:
        tools = sorted(t.name for t in (await s.list_tools()).tools)
        r = await s.call_tool("get_plan_details", {"plan_id": "PLAN-001"})
        return tools, r

    tools, r = anyio.run(_http_session, http_server, bearer("op.alex"), fn)
    assert len(tools) == 10 and "close_ticket" not in tools
    assert not r.is_error and r.structured_content["plan_id"] == "PLAN-001"


def test_http_cross_tenant_denied(http_server: str, bearer: Bearer) -> None:
    async def fn(s: ClientSession) -> Any:
        return await s.call_tool("get_ticket_details", {"ticket_id": "ZD-1001"})

    r = anyio.run(_http_session, http_server, bearer("op.casey"), fn)
    assert r.is_error and r.structured_content["error"] == "not_found"


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer not.a.token"}, {"Authorization": "Basic dXNlcjpwYXNz"}],
)
def test_http_unauthenticated_gets_401_before_mcp(
    http_server: str, headers: dict[str, str]
) -> None:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    r = httpx2.post(
        f"{http_server}/mcp",
        json=body,
        timeout=5,
        headers={**headers, "Accept": "application/json, text/event-stream"},
    )
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Bearer")
    assert "tools" not in r.text


def test_http_rejects_foreign_host_header(http_server: str, bearer: Bearer) -> None:
    """DNS-rebinding protection: a request naming another Host is refused even with a token."""
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    r = httpx2.post(
        f"{http_server}/mcp",
        json=body,
        timeout=5,
        headers={
            **bearer("op.alex"),
            "Host": "evil.example.test",
            "Accept": "application/json, text/event-stream",
        },
    )
    assert r.status_code in (400, 403, 421)


def test_http_metrics_require_admin_scope(http_server: str, bearer: Bearer) -> None:
    assert httpx2.get(f"{http_server}/metrics", timeout=5).status_code == 401
    assert (
        httpx2.get(f"{http_server}/metrics", headers=bearer("op.alex"), timeout=5).status_code
        == 403
    )
    r = httpx2.get(f"{http_server}/metrics", headers=bearer("admin.kim"), timeout=5)
    assert r.status_code == 200 and "get_plan_details" in r.json()


def _stdio_params(settings: Settings, test_db: str, token: str) -> StdioServerParameters:
    env = {
        **{k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "SYSTEMROOT")},
        "RETIREMENTOPS_MCP_TOKEN": token,
        "RO_DB_NAME": test_db,
    }
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "retirementops.mcp_servers.service_ops"],
        env=env,
        cwd=str(ROOT),
    )


def test_stdio_discovery_and_call(settings: Settings, test_db: str, dataset: Dataset) -> None:
    # A real subprocess verifies tokens with the keys in keys/ (as configured in .env).
    container = build_container(settings, db_name=test_db)
    try:
        token = container.identity.issue_dev_token("rev.sam")
    finally:
        container.dispose()

    async def go() -> tuple[list[str], Any]:
        async with (
            stdio_client(_stdio_params(settings, test_db, token)) as (read, write),
            ClientSession(read, write) as s,
        ):
            await s.initialize()
            tools = sorted(t.name for t in (await s.list_tools()).tools)
            r = await s.call_tool("get_policy_snippets", {"category": "ESCALATION"})
            return tools, r

    tools, r = anyio.run(go)
    assert len(tools) == 10
    assert not r.is_error and {x["snippet_id"] for x in r.structured_content["snippets"]} == {
        "POL-013",
        "POL-014",
    }


def test_stdio_refuses_to_start_without_valid_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("RETIREMENTOPS_MCP_TOKEN", "garbage")
    assert cli.main(["--transport", "stdio"]) == 2
    assert "missing, invalid or expired" in capsys.readouterr().err


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10"])  # noqa: S104 - asserting refusal
def test_http_refuses_non_loopback_bind(host: str) -> None:
    assert cli.main(["--transport", "http", "--host", host]) == 2


def test_http_accepts_ipv6_loopback_host_header(http_server: str, bearer: Bearer) -> None:
    """Review Low-4: every loopback bind __main__ allows must also pass DNS-rebinding protection."""
    port = http_server.rsplit(":", 1)[1]
    body = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    r = httpx2.post(
        f"{http_server}/mcp",
        json=body,
        timeout=5,
        headers={
            **bearer("op.alex"),
            "Host": f"[::1]:{port}",
            "Accept": "application/json, text/event-stream",
        },
    )
    assert r.status_code == 200
