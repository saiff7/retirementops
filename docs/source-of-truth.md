# Source-of-Truth Registry

> Synthetic simulation. Every "system" below is a clearly labelled **mock adapter**. No real Zendesk,
> record-keeping, payroll or vendor-management product is integrated.

## Principle

Every business fact has exactly one owning system. RetirementOps **reads** facts through connectors and
**proposes** changes. It never keeps a competing copy of a business fact as truth. The RetirementOps
application database owns only workflow metadata: runs, drafts, proposals, approvals and audit.

Evidence snapshots in the evidence bundle are **copies with provenance**. Each one carries a source,
record ID, version, retrieval timestamp and content hash. They exist to prove what the agent saw. They
are not authoritative, and approval re-checks the live source wherever staleness matters.

## Registry

| Capability | Owning system (simulation) | Postgres schema | Connector interface | RetirementOps may | RetirementOps may **not** |
|---|---|---|---|---|---|
| Ticket status, comments, requester, assignee, organisation | Zendesk mock | `zendesk_mock` | `TicketConnector` | Read tickets. Post an **approved** public reply. Apply an **approved** assignment. | Close or solve tickets. Delete comments. Edit requester data. |
| Employer / plan sponsor identity, tenant mapping | Plan administration mock | `planadmin_mock` | `PlanAdminConnector` | Read | Write |
| Plans and effective-dated plan rules | Plan administration mock | `planadmin_mock` | `PlanAdminConnector` | Read | Write |
| Participant enrollment, deferral elections | Plan administration mock | `planadmin_mock` | `PlanAdminConnector` | Read (minimised fields) | Modify enrollment or elections |
| Payroll files and contribution rows | Payroll mock | `payroll_mock` | `PayrollConnector` | Read and reconcile deterministically | Change, correct or re-post contributions |
| Approved legal / due-diligence answers | Versioned playbook store | `playbook` (+ R2 originals when deployed) | `PlaybookConnector` | Retrieve exact passages with version, section and effective date | Edit approved language |
| Vendor identity, risk tier, reviews, findings | Vendor registry mock | `vendor_mock` | `VendorConnector` | Read and flag | Set or alter risk tier. Close findings. |
| Agent runs, tool calls, evidence bundles, drafts, proposals, approvals, audit, asset registry, eval runs | **RetirementOps application** | `app` | n/a (application services) | Own and write | Store replacement business truth |

## Enforcement (not just convention)

1. **Separate schemas.** Each mock system lives in its own Postgres schema. Only the connector adapter
   for that system queries it. An import-linter contract forbids `agent_runtime`, `api` and `mcp_servers`
   from importing mock-system table models directly.
2. **Connector service identities.** Each connector runs as a named service identity
   (`svc-zendesk-reader`, `svc-zendesk-writer`, `svc-planadmin-reader`, `svc-payroll-reader`, and so on).
   The writer identity is used only by the execution service. Separate Postgres roles with schema-level
   `GRANT`s are planned for Milestone 1 as a stretch goal. See ADR-003.
3. **Narrow connector interfaces.** `TicketConnector` has `post_public_reply` and `apply_assignment` and
   **no** `set_status`, `close`, `solve` or `delete`. A test asserts that these methods are absent.
4. **Proposals, not writes.** Agent-facing MCP tools can create drafts and proposals in `app` and nothing
   else. See [architecture.md](architecture.md#2-identity-and-authorization-model).
