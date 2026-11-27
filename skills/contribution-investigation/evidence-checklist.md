# Evidence checklist: contribution investigation

Every item must be present, unchanged and about the resolved participant, plan and pay period.
The host checks this list; the server-side draft validator checks it again before storing a draft.

| # | Evidence | Source system | Blocks drafting when |
|---|---|---|---|
| 1 | The ticket being answered | `zendesk_mock` | always required |
| 2 | Pay period (start, end, pay date) | `payroll_mock` | unknown pay period |
| 3 | Enrollment in force on the period end date | `planadmin_mock` | none (`NOT_ENROLLED`), overlapping (`ENROLLMENT_CONFLICT`) |
| 4 | Employee deferral rule in force | `planadmin_mock` | none (`RULE_MISSING`), more than one (`RULE_CONFLICT`) |
| 5 | Employer match rule in force | `planadmin_mock` | none (`RULE_MISSING`), more than one (`RULE_CONFLICT`) |
| 6 | Exactly one payroll row for the period | `payroll_mock` | none (`MISSING_PAYROLL_ROW`), several (`MULTIPLE_PAYROLL_ROWS`) |

Each cited record carries its system, record type, ID, version, retrieval time and content hash.
A record that changed after it was read is refused.

Only `MATCH` and `DISCREPANCY` findings may be drafted. All monetary values in a draft must be the
host's formatted values from the finding.
