# Project context: RetirementOps

> **Synthetic data only.** Interview-grade portfolio simulation of AI-assisted operations at a
> fictional retirement-savings provider. Not production software. Not represented as compliant with
> ERISA, IRS rules, SOC 2, GLBA or any regulation. Mock identity, not production SSO.

## Purpose and requirements

RetirementOps shows how to engineer bounded AI workers into evidence-heavy business work, keeping
source-of-truth ownership, access control, human accountability and auditability. The source
specification is the "RetirementOps — Forward-Deployed AI Engineer Project Specification". The
milestone plan is [docs/implementation-plan.md](docs/implementation-plan.md).

- **Primary workflow (Service Operations):** triage plan-sponsor tickets, gather plan, enrollment and
  payroll evidence, calculate contributions deterministically, draft a reply, and require a human
  to approve the exact draft version before anything leaves the system.
- **Secondary workflow (Legal & Compliance, M7):** cited questionnaire answers from a versioned
  playbook, plus deterministic vendor-oversight checks.
- **Hard requirements:**
  - authorization and tenant isolation are enforced by servers, never by prompts;
  - money uses `Decimal` / integer cents, and no model arithmetic;
  - humans approve consequential actions;
  - no tool or path can close tickets, change contributions or enrollment, or move money;
  - every model, skill, tool schema and dataset version used in an eval is recorded.

Business process and failure cases: [docs/business-process-as-is-to-be.md](docs/business-process-as-is-to-be.md).
Source-of-truth ownership: [docs/source-of-truth.md](docs/source-of-truth.md).

## Architecture

```text
                Streamlit UI (M5)          MCP clients (Inspector, Claude Code, M3 orchestrator)
                       │                         │ stdio / Streamable HTTP (bearer)
          FastAPI  api/ (read-only)        mcp_servers/service_ops/ (10 tools)
                       └──────────┬──────────────┘
                     services/  (enforcement point: scopes, tenant checks, evidence, guard)
           ┌──────────────┬───────┴──────┬─────────────────┐
     connectors/      governance/      store/          domain/ (pure: money, rules,
  (one per mock     (audit chain,    (drafts,          contributions, authz, taxonomy)
   system + role)    tokens, logs,    proposals)
                     pseudonyms,
                     rate limits)
           └──────────────┴──────────────┴──→ db/ (engines per role, scoped transactions)
                                                      │
                PostgreSQL 16: zendesk_mock, planadmin_mock, payroll_mock, servicekb_mock,
                               app (users, audit, drafts, proposals), sec (RLS helper)
```

- **Layering** is machine-checked by 5 import-linter contracts: domain is pure; the API and MCP
  layers never import connectors, the store or the database directly; connector adapters are
  independent of each other.
- **Mock systems** are separate schemas, each read or written by its own Postgres login role, with
  row-level security on every tenant-owned table (ADR-003).
- **Composition root:** `services/container.py` wires engines into connectors and services.

Details: [docs/architecture.md](docs/architecture.md), the MCP server in
[docs/mcp-service-ops.md](docs/mcp-service-ops.md), and decisions in [DECISIONS.md](DECISIONS.md)
and [docs/adr/](docs/adr/README.md).

## Technology stack (installed versions)

| Area | Choice |
|---|---|
| Language and packaging | Python 3.12.14, uv (`uv.lock` committed) |
| API | FastAPI 0.165, uvicorn 0.54, Pydantic 2.14 |
| Database | PostgreSQL 16.10 in Docker (`127.0.0.1:55432`); SQLAlchemy 2.1 Core (no ORM); psycopg 3.3; Alembic 1.20 |
| MCP | MCP Python SDK 2.3.0 (low-level `Server`), pinned `mcp>=2.3,<3` |
| Identity | PyJWT 2.15, RS256 mock tokens (dev keypair in git-ignored `keys/`) |
| Logging | structlog 26.1 with a redaction processor, also applied to standard-library loggers |
| Quality | pytest 9.2, hypothesis, ruff 0.17, mypy 2.4 (strict on `domain/`), import-linter 2.17 |
| CI | GitHub Actions (`.github/workflows/ci.yml`; has not run on GitHub yet) |
| Planned | Ollama / optional Anthropic provider (M3), Streamlit (M5), Cloudflare Workers + OAuth (M6) |

## Security principles and constraints

1. **The model is not a security boundary.** Every check runs in trusted server code and,
   where possible, again in the database.
2. **Least privilege per connector.** No runtime role can bypass RLS, create objects, change ticket
   status, update or delete contributions, or approve drafts. Insert guards and immutability triggers
   protect workflow records (migrations 0003–0005).
3. **Tenant isolation is defence in depth:** an explicit filter in every query, plus RLS (fail
   closed when no scope is set), plus object-level checks. Other tenants' objects return 404.
4. **Untrusted content:** ticket text and retrieved documents are data. Requester text is labelled
   `UNTRUSTED_REQUESTER_TEXT`, and SSN-like values are masked.
5. **Evidence integrity:** cited records are re-fetched in the ticket's tenant, hash-checked, and
   allowed only if the caller may read that record type.
6. **Privacy:** synthetic aliases only; audit records hold keyed pseudonyms and route templates;
   logs are redacted; database exceptions hide bound parameters.
7. **Fail closed:** drafts are `NEEDS_MANUAL_REVIEW` until the M3 validator exists; errors become
   generic responses.
8. **Honesty:** the audit log is tamper-evident, not immutable; mock identity is not SSO; no
   compliance claims.

Data classification: [docs/data-classification.md](docs/data-classification.md).
Threat model: [docs/threat-model.md](docs/threat-model.md).
