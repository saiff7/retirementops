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
| Ticket status, comments, requester, assignee, organisation | Zendesk mock | `zendesk_mock` | `TicketReader` / `TicketWriter` | Read tickets. Post an **approved** public reply. Apply an **approved** assignment. | Close or solve tickets. Delete comments. Edit requester data. |
| Employer / plan sponsor identity, tenant mapping | Plan administration mock | `planadmin_mock` | `PlanAdminReader` | Read | Write |
| Plans and effective-dated plan rules | Plan administration mock | `planadmin_mock` | `PlanAdminReader` | Read | Write |
| Participant enrollment, deferral elections | Plan administration mock | `planadmin_mock` | `PlanAdminReader` | Read (minimised fields) | Modify enrollment or elections |
| Payroll files and contribution rows | Payroll mock | `payroll_mock` | `PayrollReader` / `PayrollIngestor` | Read and reconcile deterministically | Change, correct or re-post contributions |
| Approved legal / due-diligence answers | Versioned playbook store | `playbook` (+ R2 originals when deployed) | `PlaybookConnector` (M7) | Retrieve exact passages with version, section and effective date | Edit approved language |
| Vendor identity, risk tier, reviews, findings | Vendor registry mock | `vendor_mock` | `VendorConnector` (M7) | Read and flag | Set or alter risk tier. Close findings. |
| Service-ops policy guidance (fictional, provider-wide, effective-dated) | Service knowledge base mock | `servicekb_mock` | `PolicyKbReader` | Read versions in force | Edit policies |
| Agent runs, tool calls, evidence bundles, drafts, proposals, approvals, audit, asset registry, eval runs | **RetirementOps application** | `app` | n/a (application services) | Own and write | Store replacement business truth |

## Enforcement (not just convention)

1. **Separate schemas.** Each mock system lives in its own Postgres schema. Only the connector adapter
   for that system queries it. Import-linter contracts forbid `api` and `mcp_servers` from importing connectors, the store or
   the database directly (`agent_runtime` is added in M3).
2. **Connector service identities.** Each connector runs as its own Postgres login role
   (`svc_zendesk_reader`, `svc_zendesk_writer`, `svc_planadmin_reader`, `svc_payroll_reader`,
   `svc_payroll_ingest`, `svc_servicekb_reader`), with schema-level grants and row-level security
   (since M1; ADR-003). The writer identity is reserved for the execution service (M5).
3. **Narrow connector interfaces.** `TicketWriter` has `post_public_reply` and `apply_assignment` and
   **no** status, close, solve or delete method. A test asserts that these methods are absent.
4. **Drafts and proposals are RetirementOps records** (`app.drafts`, `app.draft_versions`, `app.action_proposals`, since M2). They describe intended changes and never change the owning system until approved and executed (M5).
5. **Proposals, not writes.** Agent-facing MCP tools can create drafts and proposals in `app` and nothing
   else. See [architecture.md](architecture.md#2-identity-and-authorization-model).
