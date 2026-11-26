# RetirementOps: AI Transformation Platform (portfolio simulation)

> ⚠️ **Synthetic data only.** This is an interview-grade prototype, not a production retirement
> platform. Every employer, plan, participant, rule, payroll record, ticket, document and vendor is
> fictional. Nothing here is legal, ERISA, IRS, investment or compliance advice. It is **not** represented
> as compliant with ERISA, IRS rules, SOC 2, GLBA or any regulation. Never connect it to real participant or
> payroll data. Identity is a **mock**, not production SSO. A real deployment would need legal,
> security, privacy, architecture and vendor-risk review.

RetirementOps shows how to engineer bounded AI workers into a retirement-savings provider's service and
compliance operations. It uses mock connectors behind stable interfaces, narrow MCP tools with
server-side authorization, versioned skills, deterministic Python for money, human approval of exact
draft versions, evaluation gates, audit and handover.

## Status

| Milestone | State |
|---|---|
| M0 Discovery & design | Done, approved 2026-11-26 |
| M1 Synthetic business backend | Done, approved 2026-11-26 |
| M2–M8 | Not started |

## Quickstart (local)

Prerequisites: Docker, [uv](https://docs.astral.sh/uv/). Postgres listens on `127.0.0.1:55432`, so it
does not clash with a local Postgres on 5432.

```bash
uv sync
```

```bash
uv run python scripts/dev_bootstrap.py
```

```bash
docker compose up -d --wait db
```

```bash
uv run python scripts/seed.py --seed 42 --reset
```

```bash
uv run uvicorn retirementops.api.main:create --factory --host 127.0.0.1 --port 8000
```

`dev_bootstrap.py` writes `.env` with random local secrets and a dev JWT keypair in `keys/`. Both are
git-ignored. Get a mock token for a seeded fictional user, for example `op.alex`, `op.casey`,
`rev.sam` or `comp.jo`:

```bash
curl -s -X POST localhost:8000/auth/dev-token -H 'content-type: application/json' -d '{"user_id":"rev.sam"}'
```

Checks (the same set CI runs):

```bash
uv run ruff check src tests scripts migrations && uv run ruff format --check src tests scripts migrations && uv run mypy && uv run lint-imports && uv run pytest -q
```

## What M1 contains

- **Mock systems as separate Postgres schemas**: `zendesk_mock`, `planadmin_mock`, `payroll_mock`,
  plus `app` for RetirementOps metadata.
- **One Postgres login role per connector**, with least-privilege grants, and **row-level security** on
  every tenant-owned table (ADR-003). No runtime role can change ticket status, change contributions or
  enrollment, or delete data.
- **Deterministic money**: `Decimal` in Python, integer cents in the database, `ROUND_HALF_UP` applied
  once. Any non-zero variance is a `DISCREPANCY` (ADR-006).
- **Reproducible seed data** (`seed=42`): 3 tenants, 5 plans, 50 participant aliases, 8 pay periods,
  371 payroll rows and 121 tickets. It includes deliberate hard cases, listed in
  [`data/synthetic/manifest.json`](data/synthetic/manifest.json).
- **Read-only FastAPI** with mock RS256 JWT identity. Roles and tenants are re-read from the directory
  on every request.
- **Hash-chained, append-only audit log**. It is tamper-evident, not immutable (ADR-009). Identifiers are pseudonymised, and anonymous write volume is bounded (ADR-012).
- **Rate limiting**: per client, per principal, and a failed-authentication block that applies before any database work (ADR-012).
- **Structured logs** with redaction and pseudonymised participant refs.

## Design docs

- [Business process: as-is / to-be](docs/business-process-as-is-to-be.md)
- [Source-of-truth registry](docs/source-of-truth.md)
- [Data classification](docs/data-classification.md)
- [Architecture](docs/architecture.md) and [decision records](docs/adr/README.md)
- [Threat model](docs/threat-model.md)
- [Implementation plan, decisions and M1 notes](docs/implementation-plan.md)

## What the agent can never do

Close or solve a ticket, send anything without human approval of the exact version and hash, approve its
own work, change contributions or enrollment, move money, perform financial arithmetic, or make a final
legal or risk determination. These limits are enforced in server code, database grants and tests, not
in prompts.
