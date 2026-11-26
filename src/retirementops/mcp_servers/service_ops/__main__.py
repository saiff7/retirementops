"""Run the Service Operations MCP server.

    # stdio (MCP Inspector, Claude Code). The token is re-verified on every call; dev tokens expire
    # after 15 minutes, so issue a fresh one with scripts/dev_token.py when it does.
    RETIREMENTOPS_MCP_TOKEN=... uv run python -m retirementops.mcp_servers.service_ops

    # Streamable HTTP on loopback only (remote exposure arrives with OAuth in M6)
    uv run python -m retirementops.mcp_servers.service_ops --transport http --port 8010

Logs go to stderr. On stdio, stdout carries only the MCP protocol.
"""

from __future__ import annotations

import argparse
import os
import sys

import anyio
import uvicorn
from mcp.server.stdio import stdio_server

from retirementops.config import get_settings
from retirementops.governance.logging import configure_logging
from retirementops.mcp_servers.service_ops.http import create_http_app
from retirementops.mcp_servers.service_ops.server import ServiceOpsMcp, TokenPrincipalSource
from retirementops.services.container import build_container
from retirementops.services.errors import Unauthenticated

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
TOKEN_ENV = "RETIREMENTOPS_MCP_TOKEN"  # noqa: S105 - an env var name, not a secret


async def _run_stdio(mcp: ServiceOpsMcp) -> None:
    async with stdio_server() as (read, write):
        await mcp.server.run(read, write, mcp.server.create_initialization_options())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="retirementops.mcp_servers.service_ops")
    ap.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--db", default=None, help="database name (default: RO_DB_NAME)")
    args = ap.parse_args(argv)

    if args.transport == "http" and args.host not in LOOPBACK:
        print("refusing non-loopback bind: remote exposure requires OAuth (M6)", file=sys.stderr)
        return 2

    container = build_container(get_settings(), db_name=args.db)
    configure_logging(container.log_pepper, stream=sys.stderr)
    try:
        if args.transport == "http":
            app = create_http_app(container, port=args.port)
            # access_log off: uvicorn would log raw client IPs; our gate logs pseudonyms.
            # log_config=None: uvicorn keeps no handlers of its own, so its records go through the
            # redacting root handler installed by configure_logging (review Low-5).
            uvicorn.run(
                app,
                host=args.host,
                port=args.port,
                access_log=False,
                log_level="warning",
                log_config=None,
            )
            return 0
        token = os.environ.get(TOKEN_ENV, "")
        try:
            container.identity.authenticate(token)  # fail fast with a clear message
        except Unauthenticated:
            print(
                f"{TOKEN_ENV} is missing, invalid or expired (see scripts/dev_token.py)",
                file=sys.stderr,
            )
            return 2
        anyio.run(_run_stdio, ServiceOpsMcp(container, TokenPrincipalSource(container, token)))
        return 0
    finally:
        container.dispose()


if __name__ == "__main__":
    sys.exit(main())
