# ADR-001: Python/FastAPI core is the only enforcement point

**Status:** Accepted (2026-11-26)

## Context
Authorization, tenant isolation, validation and approval must not depend on the model, the UI or the
edge gateway. The spec puts the MCP gateway in TypeScript on Cloudflare and the domain logic in Python.
If both enforced policy independently, the two implementations could drift.

## Decision
The Python application services are the **authoritative** enforcement point for authentication,
scopes, object-level tenant checks, schema validation, rate limits, idempotency and approval. Every
entry point calls the same service layer: FastAPI routes, the Python MCP servers, and later the
Cloudflare gateway. The gateway's checks (OAuth and scope) are **defence in depth**. They do not replace
the Python checks.

Stack: Python 3.12 (uv-managed), FastAPI, Pydantic v2 (`extra="forbid"` on all inputs), SQLAlchemy 2.0,
Alembic, PostgreSQL 16 in Docker, structlog, pytest.

## Consequences
- Every policy is written and tested once, in Python.
- The TypeScript gateway stays thin: authenticate, check scope, forward.
- Pydantic models are the single source of tool schemas. Zod schemas in the gateway are checked against
  JSON Schema exported from Pydantic by a contract test, so drift fails CI.

## Alternatives
- *Enforce in the Worker only.* Rejected, because the local path (Python MCP, UI) would be unprotected.
- *SQLModel.* Rejected. It merges ORM and API models. Keeping them separate makes the field allowlist explicit.
- *Python 3.14 (installed locally).* Deferred. 3.12 has the broadest wheel support today. Revisit later.
