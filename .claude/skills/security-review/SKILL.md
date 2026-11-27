---
name: security-review
description: Review a RetirementOps change against its own security boundaries (authorization, tenant isolation, run binding, untrusted content, deterministic money, human approval, database roles, audit). Use before asking the owner to approve a commit. Developer workflow for Claude Code, not a runtime agent skill.
---

# Security review (RetirementOps)

The model is never a security boundary; trusted Python and the database are. Review the actual
diff and code (`git diff`, the files themselves), not summaries. Do not modify files during the
review unless asked. Report findings as **critical / important / minor / confirmed safeguards**,
each with file and line, the concrete failure scenario and a minimal fix.

## Checklist

1. **Authorization:** every principal-facing service path calls `ctx.require(...)`; agent principals never get
   approval, ingest or compliance scopes (`AGENT_RUN_SCOPES`); only a human operator assigned to
   the tenant can start a run (`require_run_operator`), checked before ticket text reaches a model.
2. **Tenant isolation:** explicit tenant filter **and** RLS (`scoped_tx`) **and** object checks;
   other tenants' objects are `NotFound`; no tool takes a `tenant_id`; run tokens bind tenant,
   ticket, subjects and permitted queue, and services enforce it.
3. **Untrusted content:** ticket and document text is data, delimited and masked
   (SSN-like values, `E-` refs) before any prompt; host risk detectors flag injection independently
   of the model; the model never receives write tools or raw money values from tool results.
4. **Money:** `Decimal`/integer cents, one `ROUND_HALF_UP` (ADR-006), any non-zero variance is a
   discrepancy, no floats, no model arithmetic; drafts may only state `format_money` values from
   the server-recomputed finding (`draft-validator/2`).
5. **Human approval and side effects:** no path sends, closes, assigns, approves, changes
   contributions or enrollment, or moves money; writes create drafts/proposals only; the M5
   approval rules (ADR-007) are not weakened.
6. **Database:** new tenant tables have RLS + `tenant_isolation`; grants are per role and minimal;
   `app_service` is not widened; guard functions pin `search_path`; downgrades revoke grants;
   engines keep `hide_parameters=True`; applied migrations are never edited.
7. **Audit, logs, traces:** audit goes through `AuditService` (pseudonyms, no raw `PART-`/`E-`
   refs); logs carry no tokens, bodies or amounts; trace failures are visible
   (`trace_written`, `agent_run.trace_failed`).
8. **Versioning:** skill and validator changes bump their versions and pins.
9. **Fail closed:** errors, timeouts, budgets and malformed model output end in
   `NEEDS_MANUAL_REVIEW`; concurrency assumptions are enforced, not just documented.
10. **Secrets and data:** synthetic data only; no secrets, keys, `.env` values, personal paths or
    real identifiers in the diff.

## Verify claims with evidence

- Write probes outside the repo (for example the session scratchpad) that use the real services
  and database; run them with `PYTHONPATH=. uv run pytest -q -p tests.conftest --rootdir . <probe>`.
- Check that each new security test would fail without its fix.
- Run the full checks and the secret scan, and report exact output:

```bash
uv run ruff check src tests scripts migrations && uv run ruff format --check src tests scripts migrations && uv run mypy && uv run lint-imports && uv run pytest -q
```

```bash
uv run python scripts/check_no_secrets.py && git diff --check
```

Never claim a check passed without running it. End with an explicit recommendation (commit or
fix first) and wait for the owner.
