# Data Classification

> All data in this project is **synthetic**. The classification exists so the system behaves as if the
> data were real, and so the controls can be shown and tested.

## Levels

| Level | Definition | Handling |
|---|---|---|
| **Public** | Safe to publish. | No restriction. |
| **Internal** | Operational data with low harm if disclosed. | Authenticated staff. May appear in logs. |
| **Confidential** | Business or tenant data whose disclosure harms a tenant or the provider. | Tenant-scoped access. Not in logs, except as IDs or hashes. Minimised in prompts. |
| **Restricted** | Personal financial data, credentials or secrets. | Field allowlists. Never in logs. Minimised or excluded from model prompts. Secrets never stored in DB or Git. |
| **Prohibited** | Must never enter the system. | Rejected at ingest. The offending upload is not persisted (see below). |

## Field inventory

| Entity.field | Level | In tool output? | In model prompt? | In logs? | Notes |
|---|---|---|---|---|---|
| `tenant.id`, `tenant.display_name` | Internal | Yes | Name only if needed | ID only | Fictional company names |
| `plan.id`, `plan.name`, `plan_rule.*` | Internal | Yes | Yes | ID + rule_version | Rules labelled fictional |
| `participant.ref` (e.g. `PART-0001`) | Confidential | Yes | Yes | Hashed | Synthetic alias, never a name. Audit: pseudonym only. |
| `participant.employer_employee_ref` | Confidential | Only for resolution | No | No | Used by the host to resolve identity |
| `participant.name`, `dob`, `address`, `email` | **Prohibited** | n/a | n/a | n/a | Not modelled at all |
| `ssn`, `tax_id`, national IDs | **Prohibited** | n/a | n/a | n/a | Upload rejected if a column looks like one |
| Bank account / routing numbers | **Prohibited** | n/a | n/a | n/a | Upload rejected |
| `enrollment.status`, `enrollment.deferral_pct`, `effective_date` | Confidential | Yes | Yes | No | |
| `payroll_row.eligible_pay_cents` | **Restricted** | Yes, to authorised tenant scope | Yes, as a formatted value from the finding only | No | Compensation data |
| `payroll_row.employee_contribution_cents`, `employer_contribution_cents` | **Restricted** | Yes | Yes (formatted, from finding) | No | |
| `ticket.subject`, `ticket.body`, comments | Confidential, and **untrusted input** | Yes | Yes, delimited as untrusted | No (hash + length only) | May contain injected instructions or pasted PII |
| `ticket.requester_email` | Confidential | No | No | No | Synthetic `@example.test` |
| `document_passage.text` (playbook) | Internal | Yes | Yes | Passage ID | Approved language |
| `vendor.*`, `vendor_finding.*` | Confidential | Yes | Yes | IDs | |
| `draft.body` | Confidential | Yes (to reviewers) | n/a | Hash + version | |
| Access tokens, API keys, signing keys, DB passwords | **Restricted (secret)** | Never | Never | Never (redacted) | Environment / Wrangler secrets only |
| `principal.id`, `role`, `scopes` | Internal | n/a | No | Yes | |
| Client network address | Confidential (personal data) | n/a | No | Pseudonym only | Rate-limit key; never stored raw |
| `correlation_id`, `run_id` | Internal | Yes | No | Yes | |

## Controls tied to classification

1. **Field allowlists in tools.** Each MCP tool's output model lists exactly the fields it returns.
   Extra fields are never serialised (`model_config = ConfigDict(extra="forbid")` on input models, and
   explicit output models built from allowlisted fields).
2. **Prompt minimisation.** The orchestrator builds prompts from the evidence bundle's minimised view,
   not from raw connector records. Monetary values reach the model only as preformatted strings taken from
   the deterministic finding.
3. **Log redaction.** A structured-logging processor drops any key in a deny set (`body`, `text`,
   `token`, `authorization`, `*_cents`, `email`, ...) and hashes participant refs. A test asserts that
   seeded canary values (a fake token, a canary ticket body) never appear in captured logs.
4. **Upload rejection.** Synthetic payroll CSV upload (Milestone 1+) checks headers against an allowlist
   and scans values for SSN-like and bank-account-like patterns. On rejection the whole file is refused,
   **no rows are persisted**, and only `{filename_hash, reason, row_count}` is audited.
5. **Audit pseudonymisation (ADR-012).** Audit rows store `pref_…`, `eref_…` and `client_…` HMAC pseudonyms under a dedicated audit key, plus route templates instead of raw paths. The audit writer refuses any raw participant ref, employee ref or SSN-like value.
6. **Pasted PII in tickets.** If a synthetic ticket body contains an SSN-like pattern, the ticket is
   flagged `contains_restricted_pattern`. The pattern is masked before the body is placed in any prompt,
   and the run is forced to manual review.
7. **Retention (documented, not enforced in the demo).** A real deployment would need a retention
   schedule for drafts, evidence snapshots and audit. This is listed as a known limitation.
