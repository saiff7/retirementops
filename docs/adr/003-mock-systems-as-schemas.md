# ADR-003: Mock systems as separate Postgres schemas behind connectors, with per-connector DB roles and row-level security

**Status:** Accepted (2026-11-26). Amended the same day: database roles per connector are **mandatory in M1**, no longer a stretch goal.

## Context
The spec needs authoritative business records to live in "their owning mock systems". It also lists
all tables, business ones included, in one database. Running five separate mock services would be
realistic but heavy for a portfolio project. The project owner asked that tenant isolation and
least privilege are not traded for speed.

## Decision
One Postgres instance with one schema per owning system: `zendesk_mock`, `planadmin_mock`,
`payroll_mock`, `app` (RetirementOps workflow metadata) and `sec` (shared security helper functions).
`vendor_mock` and `playbook` are created in M7, when they get real content.

- Only the adapter for a system (`connectors/zendesk_mock.py`, ...) maps that schema's tables.
- Each adapter implements a typed interface (`TicketReader`, `TicketWriter`, `PlanAdminReader`,
  `PayrollReader`, `PayrollIngestor`) and returns frozen Pydantic DTOs. Callers never see raw rows.
- Each adapter call returns a `SourceRef` for evidence: system, record type, record ID, version,
  retrieval time and content hash.

### Database roles (M1, mandatory)

| Postgres role | Used by | Privileges |
|---|---|---|
| `ro_owner` | Alembic migrations, seed loader only | Owns all schemas and tables. **Never used at runtime by the API.** |
| `svc_zendesk_reader` | `ZendeskMockReader` | `SELECT` on `zendesk_mock.*` |
| `svc_zendesk_writer` | `ZendeskMockWriter` (execution service, M5) | `SELECT` on tickets and comments; `INSERT` on `ticket_comments` and `ticket_events`; column-level `UPDATE (assignee_queue, updated_at, version)` on `tickets`. **No `UPDATE` on `status`.** |
| `svc_planadmin_reader` | `PlanAdminMockReader` | `SELECT` on `planadmin_mock.*` |
| `svc_payroll_reader` | `PayrollMockReader` | `SELECT` on `payroll_mock.*` |
| `svc_payroll_ingest` | `PayrollMockIngestor` | `SELECT` + `INSERT` on `payroll_files` and `payroll_rows` |
| `app_service` | RetirementOps app (`app` schema) | `SELECT` on `app.users`; `SELECT` + `INSERT` on `app.audit_events`. **No access to any mock schema.** |

Every service role is `NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE`, and no role is granted to
another. `CONNECT` on the database is revoked from `PUBLIC`.

### Row-level security (M1, mandatory)

Every tenant-owned table in the mock schemas has RLS enabled. A policy requires `tenant_id` to be in
`sec.tenant_scope()`, which reads the transaction-local setting `retirementops.tenant_ids`. Connectors
set that value with `set_config(..., true)` (transaction-local, so it is safe with pooled connections)
from the **verified** principal's tenant assignments. When the setting is unset or empty, no rows are
visible. That is the fail-closed default. The same policy is the `WITH CHECK` for inserts.

Application code **also** filters by tenant explicitly. RLS is the second, independent layer, so one
forgotten `WHERE` clause cannot leak another tenant's data.

### Ticket status protection
- `svc_zendesk_writer` has no column privilege on `tickets.status`.
- A `BEFORE UPDATE` trigger rejects any status change made by a role other than the schema owner.
  The owner stands in for "a human acting in Zendesk itself".

### Not-found vs forbidden
Reading an object that belongs to another tenant returns **404**, not 403. Under RLS the object is
invisible, and a 404 does not reveal that it exists. Requests that name a scope the principal lacks
(a missing OAuth scope or role) return **403**. Both are written to the audit log as `access.denied`
policy decisions.

## Consequences
- A swap to a real sandbox (for example a Zendesk trial) replaces one adapter, not callers.
- Cross-schema joins are impossible for service roles because none has access to two mock schemas.
  Reconciliation happens in Python.
- Six connection pools in the API process. Each is small, which is fine for the demo.
- RLS limits what Postgres shows each role. It does not authenticate anyone: a stolen role password with
  a forged `set_config` call could read any tenant. That is why service-role credentials are secrets
  and the API is the only holder.

## Alternatives
- *Separate mock HTTP services.* More realistic network boundaries, but triples the ops surface. This can be revisited for one system (Zendesk) if the demo benefits.
- *One flat schema with one app role.* Rejected. It blurs source-of-truth ownership and gives no defence in depth.
