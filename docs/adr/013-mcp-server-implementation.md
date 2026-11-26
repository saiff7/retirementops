# ADR-013: Service Operations MCP server on the SDK 2.x low-level server; fail-closed drafts

**Status:** Accepted, and implemented. Approved by the owner on 2026-11-26 and committed with M2 as
`91f959b`. Revised the same day, before that commit, after the
independent M2 review (findings H-1, M-1 to M-3, Low-1 to Low-9), and again after the final review
(finding N-1 and related lows). See the end of this record.

## Context
ADR-004 decided to build the Python MCP servers first. At M2 the current MCP Python SDK is **2.3.0**,
whose low-level `Server` takes handlers as constructor arguments (`on_list_tools`, `on_call_tool`)
rather than the 1.x decorators. Its high-level `FastMCP` builds argument models from function
signatures, which by default ignore unknown fields. The spec requires rejecting extra fields.

## Decision
1. **Low-level `Server`, our own validation.**
   - Each tool has a Pydantic input model (`extra="forbid"`, bounded strings and lists, strict
     ints), and the advertised JSON Schema is generated from it with `additionalProperties: false`.
   - The server validates arguments itself and never trusts the client. Errors report locations
     and error types, never values.
   - The dependency is pinned to `mcp>=2.3,<3`, because the 1.x API is incompatible (M-3).
2. **Scope-filtered discovery.** `tools/list` returns only the tools whose scopes the caller holds.
   `tools/call` checks again, and the service layer checks a third time.
3. **Transports.**
   - **stdio:** the process token is re-verified on every call.
   - **Streamable HTTP:** `StreamableHTTPSessionManager(stateless=True, json_response=True)` with
     DNS-rebinding protection (Host and Origin allowlist for every loopback form, including
     `[::1]`, per Low-4), a 256 KiB body cap, and a pure-ASGI gate for rate limits and bearer auth
     in front.
   - HTTP binding is restricted to loopback until OAuth (M6). The SDK's built-in OAuth
     resource-server support is deliberately not used until a real issuer exists in M6.
4. **Policy snippets** are a new mock system, `servicekb_mock` (provider-wide, not tenant data), read
   by its own role `svc_servicekb_reader`. Retrieval returns only versions in force. Access uses
   `tickets:read`, as internal guidance for service-ops readers.
5. **Drafts and proposals move from M5 to M2 (tables only)**, because M2's acceptance requires the
   create tools to write rows. Approvals stay in M5. Integrity is enforced in the database, not
   only in code (migrations 0003 and 0004). For any role other than the schema owner:
   - drafts can only be inserted as `NEEDS_MANUAL_REVIEW`. `PENDING_REVIEW` is refused until M3,
     where the migration that adds the server-side validator re-allows it;
   - proposals can only be inserted as `PROPOSED`;
   - a draft version can only be **version 1**, and only with the same author, the same author
     kind (`human` or `service`, recorded on the header as `created_by_kind`) and the same creation
     time as its header, so it is written together with the header;
   - every guard function pins `search_path = pg_catalog, pg_temp` and uses
     `pg_catalog.pg_namespace`, including the M1 ticket-status guard. Runtime roles also have no
     TEMP or CREATE privilege, so catalog shadowing is impossible twice over;
   - `UPDATE`/`DELETE` on versions and `TRUNCATE` on all three tables are refused by triggers, even
     for the owner (Low-1);
   - there is no `UPDATE` or `DELETE` grant at all.
6. **Fail-closed draft status.** The server-side validator (claims, amounts and identities against
   ground truth) is M3. Until a draft passes it, every draft, from any client, is
   `NEEDS_MANUAL_REVIEW` with reason `SERVER_VALIDATOR_NOT_YET_AVAILABLE`. The validator will run
   inside `DraftingService`, before the insert, so external MCP clients get the same treatment, and
   it can set `PENDING_REVIEW` at creation without needing an `UPDATE` grant.
7. **Evidence integrity.** A reply must cite the ticket it answers (Low-7). Every cited record is
   re-fetched through its connector, scoped to the ticket's tenant only, and its content hash
   compared. The caller must hold the read scope for each cited record type (M-1). Without it, the
   record is never looked up and the answer is identical to "not found", so the verifier is not an
   existence oracle. Invented, other-tenant, altered, stale, duplicate, unsupported,
   unreadable-by-caller or not-in-force evidence is refused before anything is stored.
8. **Safe retries, per user.** `create_reply_draft` accepts an optional `idempotency_key`.
   - Keys are unique per `(tenant_id, created_by, idempotency_key)` (N-1), and only the same
     authenticated user's own earlier request is ever replayed.
   - Another user's key is simply not found, so that request is handled as new, with no signal
     that the key exists.
   - Before any replay lookup, on every path, the caller's read scope is checked for every cited
     evidence type, without touching the records. The answer for unreadable evidence is identical
     whether or not a matching draft exists.
   - A replay requires the same ticket, content hash and `skill_version`. It returns the original
     draft (`replayed: true`) without re-verifying evidence or writing a second audit event.
     Anything else is refused as "different draft".
9. **Bounded observability.**
   - Metrics are keyed by registered tool names only; unknown names share one `<unknown>` bucket
     (M-2).
   - Standard-library loggers (SDK, uvicorn, SQLAlchemy) are routed through the same redaction as
     our own events, including passwords in connection URLs and IPv4 addresses (Low-5).
   - All engines use `hide_parameters=True`, so bound values never appear in database exception
     text (final-review Low-1).
10. **Requester text is untrusted everywhere.** Subjects and bodies are returned as
    `{trust: "UNTRUSTED_REQUESTER_TEXT", text}`. Formatted SSNs and bare 9-digit runs are masked in
    output and refused in drafts and rationales (Low-9).

## Obligations this places on M3 and M5
- **M3:** run-scoped agent tokens carry minimum scopes. Because of decision 7, they can cite only
  what they can read. The validator runs before the insert, and the same M3 migration re-allows
  `PENDING_REVIEW` in `app.guard_draft_insert` (it must keep the pinned `search_path`). The rules engine's routing result must
  be checked against `propose_ticket_assignment` on the server, because today the tool accepts any
  valid queue (open item, Low-7b).
- **M5:** human edits (version ≥ 2), status transitions, approvals and execution are written by a
  **separate database role** (for example `svc_review_writer`) that the agent-facing path never
  holds. It gets column-level `UPDATE (status, status_reasons)` and `INSERT` on versions and
  approvals, and the migration 0003 guards are extended to name it. `app_service` stays
  insert-only for initial states. A status-transition trigger encoding the ADR-007 state machine
  is added then.

## Trade-offs and limits (stated honestly)
- **The database cannot authenticate principals.** `app_service` stamps `created_by`,
  `created_by_kind` and `author_principal` from the verified token. The 0003 guards stop a bug or a compromised process
  from creating approved records or appending versions, but a fully compromised `app_service`
  could still create a *new* version-1 draft under another principal's name. The M5 role split
  ensures that this role still cannot approve or execute anything.
- **Tamper-resistant, not immutable.** The owner can disable triggers, as with the audit table
  (ADR-009).
- **Cross-system consistency.** `app.drafts.ticket_id` cannot be a foreign key to the separate
  Zendesk mock (ADR-003). Ticket-to-tenant consistency is enforced by the service, which reads the
  ticket under RLS before writing (Low-6, accepted).
- **Rollback deletes data.** Downgrading 0002 drops all drafts and proposals. See
  [operations-migrations.md](../operations-migrations.md).

## Review findings and resolution

| Finding | Resolution |
|---|---|
| H-1 pre-approved or forged workflow rows | Migration 0003 insert guards; M5 role split required |
| M-1 evidence bypassed read scopes | `REQUIRED_SCOPE` per record type; no lookup without scope |
| M-2 unbounded metric keys | `<unknown>` bucket |
| M-3 loose SDK pin | `mcp>=2.3,<3` |
| Low-1 TRUNCATE | Triggers on all three tables |
| Low-2 plan inconsistency | Plan corrected |
| Low-3 migration order and rollback | `scripts/db_upgrade.py`, 0002 downgrade revokes its grants, round-trip test, runbook |
| Low-4 `[::1]` | Added to the host and origin allowlists |
| Low-5 stdlib logs | Routed through the redactor |
| Low-6 ticket/tenant at DB level | Accepted (see trade-offs) |
| Low-7 empty evidence / any queue | Ticket evidence now required; queue check deferred to the M3 rules engine |
| Low-8 retries | Optional per-tenant idempotency key |
| Low-9 untrusted subjects, 9-digit IDs | Trust wrapper on subjects; bare 9-digit masking and rejection |
| **Final review** N-1 replay skipped scope checks and crossed users | Per-user unique keys (0004); replay only for the same user; scope pre-check before any lookup on every path; another user's key is not found |
| Final review Low-1 parameters in exception text | `hide_parameters=True` on all engines |
| Final review Low-2 author kind, early PENDING_REVIEW | `created_by_kind` on the header, matched by version 1; M2 inserts `NEEDS_MANUAL_REVIEW` only |
| Final review Low-3 unpinned guard search paths | `search_path = pg_catalog, pg_temp` and qualified catalogs on all six trigger functions (0004) |
| Final review Low-4 replay ignored `skill_version` | Included in the replay comparison |
| Final review Low-5 runbook wording | Corrected (`CONNECT` remains after downgrade) |
| Final review Low-6 DB outage on auth returns 500 | Accepted: fails closed, generic body, redacted logs |
| Final hardening: `sec.tenant_scope()` unpinned | Migration 0005 pins `search_path = pg_catalog` and qualifies every function, type and operator; behaviour, owner and grants unchanged; downgrade restores the exact 0001 definition |

## Consequences
- The tool contract lives in one place (the Pydantic models) and is test-checked. The M6 Worker's Zod
  schemas will be contract-tested against the exported JSON Schema (ADR-004).
- The agent cannot act on any tenant until M3 run tokens exist.
- Drafts created in M2 can never be approved, by design.
