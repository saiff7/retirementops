# ADR-004: Python MCP servers first; Cloudflare Worker as a thin authenticated gateway

**Status:** Accepted (2026-11-26)

## Context
The spec wants a remote MCP gateway on Cloudflare Workers (TypeScript, Agents SDK, Streamable HTTP).
It also allows building and testing a local Python MCP implementation first, so that transport concerns
do not get mixed into domain logic.

## Decision
1. **M2:** two Python MCP servers built with the official MCP Python SDK. `service_ops` and `compliance`
   have separate tool registries and separate required scopes. Each tool is a thin wrapper:
   validate the Pydantic input, resolve the principal from the bearer token, call `services/`, and return
   the allowlisted output with source metadata. Transports are stdio (Inspector, Claude Code) and
   Streamable HTTP (orchestrator, remote tests). The servers are stateless per request.
2. **M6:** a TypeScript Worker using the Cloudflare Agents SDK and `createMcpHandler`, behind the Workers
   OAuth Provider. It re-exposes the **same tool names and schemas**, checks the OAuth scope per tool, and
   forwards to the Python API over HTTPS with a gateway service credential and the verified principal.
   The Python side re-authorises (ADR-001).
3. Tool schemas are exported from Pydantic to JSON Schema in `contracts/`. A contract test fails if the
   Worker's Zod schemas or tool list differ.
4. The exact SDK APIs are checked against current documentation when each milestone starts. Nothing is
   copied from older examples.

## Consequences
- Milestones 2 to 5 run fully offline.
- The tool list is declared in two languages, and the contract test guards it.

## Alternatives
- *Worker-only MCP.* Rejected for now. Local development and tests would need Wrangler and a tunnel to reach the Python API.
- *Generate the Worker from Python schemas.* Possible later. Overkill for about 17 tools.
