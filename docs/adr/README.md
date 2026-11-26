# Architecture Decision Records

Format: Context, Decision, Consequences, Alternatives considered. Status is one of `Proposed`,
`Accepted` or `Superseded by ADR-NNN`. All records below were accepted by the project owner on 2026-11-26.

| ADR | Title | Status |
|---|---|---|
| [001](001-trusted-python-core.md) | Python/FastAPI core is the only enforcement point | Accepted |
| [002](002-single-python-package-layout.md) | Single Python distribution instead of multiple `packages/` | Accepted |
| [003](003-mock-systems-as-schemas.md) | Mock systems as separate Postgres schemas behind connectors | Accepted |
| [004](004-mcp-python-first-cloudflare-gateway-later.md) | Python MCP servers first; Cloudflare Worker as a thin authenticated gateway | Accepted |
| [005](005-explicit-orchestrator-and-providers.md) | Explicit orchestrator; Ollama, Anthropic and Scripted providers | Accepted |
| [006](006-deterministic-money.md) | Decimal + integer cents + explicit rounding | Accepted |
| [007](007-two-phase-approval.md) | Two-phase approval bound to version + hash; no close capability | Accepted |
| [008](008-identity-and-tokens.md) | Mock JWT identity locally; OAuth 2.1 at the Cloudflare edge | Accepted |
| [009](009-audit-hash-chain.md) | Append-only audit with hash chain: tamper-evident, not immutable | Accepted |
| [010](010-evaluation-and-ci-gates.md) | Safety gates in CI use a scripted provider; model-quality evals are run and recorded locally | Accepted |
| [011](011-async-deferred-to-cloudflare.md) | Synchronous local runs; Queues and Workflows come in M6+ | Accepted |
| [012](012-rate-limiting-and-bounded-audit.md) | Rate limiting, bounded anonymous audit, pseudonymised audit records | Accepted |
| [013](013-mcp-server-implementation.md) | Service Ops MCP server on SDK 2.x low-level server; fail-closed drafts; evidence integrity | Accepted, implemented (M2, `91f959b`) |
