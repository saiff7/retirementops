# Service Operations MCP server (M2)

> Synthetic data only. Mock identity, not production SSO. Loopback only until OAuth (M6).

The server exposes ten narrow tools from spec §6 over **stdio** and **Streamable HTTP** (stateless,
JSON responses), built on the MCP Python SDK **2.3** low-level `Server` (ADR-013). Tools are thin
adapters: they validate arguments, check scopes, call application services (which re-check scopes
and tenant ownership, under Postgres RLS), and return structured data with source metadata.

## Tools

| Tool | Scopes | Writes? | Notes |
|---|---|---|---|
| `search_tickets` | `tickets:read` | No | Caller's tenants only. Summaries without body; `subject` is `{trust: UNTRUSTED_REQUESTER_TEXT, text}`. `page_size` 1–25 (default 10). |
| `get_ticket_details` | `tickets:read` | No | Subject and body returned as `{trust: UNTRUSTED_REQUESTER_TEXT, text}`, with SSN-like values and bare 9-digit IDs masked and flagged. |
| `get_plan_details` | `plans:read` | No | |
| `get_participant_enrollment` | `plans:read` | No | Plan and participant must belong to the same tenant. No employer employee number is returned. |
| `get_payroll_contributions` | `payroll:read` | No | Reported rows only. Amounts as decimal strings plus display. No calculation. |
| `get_applicable_plan_rule` | `plans:read` | No | `SELECTED`, `MISSING` or `CONFLICT` with candidate rule IDs. Never resolves a conflict. |
| `get_policy_snippets` | `tickets:read` | No | Fictional service policies **in force today** only. `limit` 1–10 (default 5). |
| `create_reply_draft` | `drafts:write`, `tickets:read` | Draft rows only | Never sends. Evidence must include the ticket being answered. Every ref is re-fetched in the ticket's tenant, hash-checked, and allowed only if the caller can read that record type. Optional `idempotency_key` makes retries safe; keys are per user, and only your own earlier request is replayed. Stored as `NEEDS_MANUAL_REVIEW` until the M3 validator exists. |
| `propose_ticket_assignment` | `drafts:write`, `tickets:read` | Proposal row only | Never assigns. An identical open proposal is returned instead of duplicated. |
| `get_ticket_action_status` | `tickets:read` | No | Proposal status, tenant-scoped. |

No tool closes, solves or deletes; sends messages; approves; changes contributions or enrollment;
moves money; or runs arbitrary SQL or HTTP fetches (`tests/unit/test_mcp_registry.py`).

`tools/list` returns only the tools whose scopes the caller holds. Compliance and admin users see none.

## Request path (`tools/call`)

1. **HTTP only:** the per-client rate limit and failed-auth block run, then bearer verification.
   Failures get `401 WWW-Authenticate: Bearer` before any MCP processing (ADR-012). DNS-rebinding
   protection is on, and binding is loopback only.
2. The principal is resolved. On HTTP it comes from the gate. On stdio, the process token is
   **re-verified on every call**, so deactivation or tenant removal takes effect immediately.
3. The principal's request budget is checked.
4. The tool name is looked up. Unknown names (for example `close_ticket`) get `unknown_tool` and an
   audit record.
5. Required scopes are checked. A failure gets `forbidden` and an audit record.
6. Arguments are validated with Pydantic: `extra="forbid"`, length and pattern limits, strict
   integers, and a 64 KiB cap. Errors name locations and error types only, never submitted values.
7. The service is called in a worker thread. `NotFound` covers both missing and other-tenant
   objects. Denials are audited with pseudonymised identifiers.
8. Every call is logged as `mcp_tool_call` (tool, principal, tenants, correlation ID, latency,
   outcome, policy decision) and counted in metrics (`GET /metrics`, `admin:ops` only). Tokens,
   argument values and ticket text are never logged. Unknown tool names share one `<unknown>` metric.
   Third-party log records (SDK, uvicorn) pass through the same redaction.

Errors are tool results with `isError: true` and `{"error": code, "message": ...}`. The codes are
`unauthenticated`, `rate_limited`, `unknown_tool`, `forbidden`, `invalid_arguments`, `not_found`,
`invalid_request` and `internal_error`.

## Run it

Prerequisites are the M1 quickstart (Docker Postgres up, dev database seeded).

```bash
export RETIREMENTOPS_MCP_TOKEN=$(uv run python scripts/dev_token.py op.alex)
```

```bash
uv run python -m retirementops.mcp_servers.service_ops
```

That is stdio. For Streamable HTTP at `http://127.0.0.1:8010/mcp`, with bearer auth per request:

```bash
uv run python -m retirementops.mcp_servers.service_ops --transport http --port 8010
```

Dev tokens expire after 15 minutes. Re-run `dev_token.py` when calls start returning
`unauthenticated`. For Claude Code, copy `.mcp.json.example` to `.mcp.json` (git-ignored) and paste
in a token.

## Verify with MCP Inspector

```bash
npx -y @modelcontextprotocol/inspector@2.14.0 --cli -e RETIREMENTOPS_MCP_TOKEN=$RETIREMENTOPS_MCP_TOKEN --cwd "$PWD" --method tools/list -- uv run python -m retirementops.mcp_servers.service_ops
```

```bash
npx -y @modelcontextprotocol/inspector@2.14.0 --cli http://127.0.0.1:8010/mcp --transport http --header "Authorization: Bearer $RETIREMENTOPS_MCP_TOKEN" --method tools/call --tool-name get_plan_details --tool-arg plan_id=PLAN-001
```

A recorded run is in [mcp-inspector-transcript.md](mcp-inspector-transcript.md). Passing the token
with `-e` puts it on the Inspector's command line, which is acceptable only for a short-lived local
dev token.

## Known limits (M2)

- The **agent principal has no tenants yet.** `agent.worker` sees nothing, which is fail-closed.
  Run-scoped agent tokens bound to one ticket's tenant arrive with the orchestrator in M3.
- **Drafts are always `NEEDS_MANUAL_REVIEW`.** The server-side claim, amount and identity validator
  is M3. Evidence refs are checked for existence, tenant and freshness now, but not for semantic
  fit.
- Proposals and drafts are insert-only, enforced by database guards (migration 0003). Approval and
  execution, with their own database role, are M5.
- Proposed queues are not yet checked against the routing rules engine (M3).
- Metrics are in-process and reset on restart.
- The Inspector's interactive mode was not exercised, only CLI mode. HTTP clients that expect OAuth
  discovery will try to sign in after a 401, because OAuth lands in M6.
