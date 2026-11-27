---
name: add-mcp-tool
description: Add or change a tool on the RetirementOps Service Operations MCP server (Pydantic input, scopes, service call, registry and safety tests). Use when a new narrow read or a new draft/proposal-only capability is needed. Developer workflow for Claude Code, not a runtime agent skill.
---

# Add an MCP tool (RetirementOps)

Tools are thin: validated input, explicit scopes, one service call. All policy lives in
`services/` (ADR-001). Read `CLAUDE.md` "MCP rules" first; this skill adds the checklist.

## Decide first

- Is it narrow and necessary? Prefer extending an existing read over a new tool.
- It must never close/solve tickets, send messages, approve anything, change contributions or
  enrollment, move money, run SQL or fetch URLs (`FORBIDDEN_TOOLS` in
  `tests/unit/test_mcp_registry.py`). Writes may only create drafts or proposals for human review.
- Changing the tool set changes the spec's §6 contract: get the owner's approval and record it.

## Steps

1. **Input model** in `mcp_servers/service_ops/schemas.py`: subclass `_In` (`extra="forbid"`),
   typed IDs with patterns (`TicketId`, `PlanId`, ...), bounded strings and lists. No `tenant_id`
   argument, ever: tenancy comes from the verified principal.
2. **Output:** an explicit allowlisted dict with a `source` block for every record. Wrap requester
   text as `UNTRUSTED_REQUESTER_TEXT` and mask restricted patterns. Money through `schemas.money()`
   (amount, currency and display).
3. **Service method** (`services/`): `ctx.require(...)` the scopes, check object-level tenancy
   (other tenants are `NotFound`, never `Forbidden`), and apply the run binding for agent runs
   (`ctx.bound_ticket/plan/participant/pay_period`, `ctx.not_in_run`, `ctx.permitted_queue`).
4. **Register** a `ToolSpec` in `mcp_servers/service_ops/tools.py` with scopes, `read_only`,
   `idempotent` and a handler that only calls the service. Add `UNTRUSTED_NOTE` if it returns
   ticket text.
5. **Agent exposure is separate:** a tool is offered to the model only if a runtime skill lists it
   in `model_tools` (read-only tools only; never `search_tickets` or `get_ticket_details`). Write
   tools can only be `host_actions`, run by the orchestrator with a short-lived write token.
   Changing a runtime skill means bumping its version and its pin in `tests/unit/test_skill_loader.py`.

## Tests

- Update `SPEC_TOOLS` in `tests/unit/test_mcp_registry.py` deliberately; keep
  `test_registry_has_no_forbidden_tools` and the schema tests (`extra` fields rejected, limits).
- Integration (`tests/integration/test_mcp_tools.py`): happy path, missing scope → 403 and
  audited, cross-tenant → `not_found`, invalid arguments rejected without echoing values.
- If agents may use it: a run-binding test in `tests/security/test_m3_run_binding.py` proving
  references outside the binding are `not_found`.
- Writes: prove nothing is sent or applied (`sent: false` / `applied: false`) and no mock-system
  row changes.

## Verify

```bash
uv run ruff check src tests scripts migrations && uv run ruff format --check src tests scripts migrations && uv run mypy && uv run lint-imports && uv run pytest -q
```

Optionally check discovery with the MCP Inspector (`docs/mcp-service-ops.md`). On stdio, stdout
is the protocol: log to stderr only. Update `docs/mcp-service-ops.md` and the root documents.
