# ADR-002: Single Python distribution instead of multiple `packages/`

**Status:** Accepted (2026-11-26)

## Context
The spec suggests `apps/api`, `apps/ui` and `packages/{domain,connectors,agent_runtime,mcp_client,governance,evaluations}`.
As separate installable packages, that means a uv workspace with roughly 8 `pyproject.toml` files,
cross-package versioning and editable installs. The project has one deployable Python service plus a UI,
so that overhead buys nothing yet.

## Decision
Use one distribution, `src/retirementops/`, with subpackages that mirror the spec's names
(`domain`, `connectors`, `services`, `api`, `mcp_servers`, `mcp_client`, `agent_runtime`, `governance`,
`evaluations`, `ui`). Enforce the layer boundaries with **import-linter** contracts in CI. For example,
`domain` imports nothing internal, and `agent_runtime` may not import `connectors`.

Additions to the spec's list:
- `services/` holds application services shared by the API and the MCP servers (see ADR-001).
- `mcp_servers/` is the Python MCP implementation the spec asks for before the Cloudflare adapter.

Everything else (`docs/`, `skills/`, `.claude/skills/`, `cloudflare/`, `data/`, `evals/`, `migrations/`,
`scripts/`, `tests/`) follows the spec.

## Consequences
- One `pyproject.toml`, one lockfile, one test run.
- Boundaries are machine-checked rather than only implied by folder layout.
- Splitting into a workspace later is mechanical if a component needs independent deployment.

## Alternatives
- *The spec layout as a uv workspace.* Workable, and the fallback if the owner prefers it. Costs more setup and gives no extra boundary guarantee beyond what import-linter already checks.
