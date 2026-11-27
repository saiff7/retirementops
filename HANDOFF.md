# Handoff

_Last updated: 2026-11-26, after the M2 commit and its documentation commits `f4d3349` and `3d281b6`._

## Current state in one paragraph

M0, M1 and M2 are completed, reviewed, approved and committed on `main`. M2 (the Service
Operations MCP server) went through three independent security reviews, with every finding fixed
or explicitly accepted. The owner approved it, and it was committed as `91f959b`. The
documentation recording that was committed separately as `f4d3349` and corrected in `3d281b6`.
GitHub push status is unverified: no push has been verified and no fetch has been performed.
The owner approved starting M3 on 2026-11-26; **M3 is in progress** (stages 1–5 of 7, uncommitted).

## Git

| | |
|---|---|
| Branch | `main` (the only branch) |
| Local commits | As of this update, oldest first: `ac9e36b`, `91f959b`, `f4d3349`, `3d281b6` (run `git log` for any later ones) |
| Documentation correction commit | `3d281b64f0c35e9c1c1e733bd78cf58f02bc2a00`, "Docs: correct project status and handoff" (six documentation files; no source code, tests or SQL) |
| Documentation commit | `f4d3349de364160a19af6551d53fb9d8ca564648`, "Docs: finalize M2 project handoff" (eight documentation files, including the migration 0005 docstring date; no source code, tests or SQL) |
| M2 commit | `91f959b2110b8210a10efc3ad7e072c8e9cfae8b`, "M2: add hardened service operations MCP server" (69 files, +6,487 / −128) |
| M0 + M1 commit | `ac9e36b47feab8b9a58f1c3c82add8cdc6bbf853`, "M0 + M1: design docs and synthetic business backend" |
| Remote | `origin` = `github.com/saiff7/retirementops`. **Push status unverified:** no push has been verified and no fetch has been performed, so the local repository has no remote-tracking information. Whether GitHub holds any of these commits has not been checked. |
| Working tree | When this was written, the only uncommitted change was a six-file follow-up documentation correction (`README.md`, `docs/implementation-plan.md`, `CURRENT_STATUS.md`, `HANDOFF.md`, `KNOWN_ISSUES.md`, `PROJECT_CONTEXT.md`), pending owner review. Run `git status` for the current state. |

Never commit `.env`, `keys/` or `.mcp.json` (all git-ignored). `scripts/check_no_secrets.py` must
pass before any commit.

## What the M2 commit contains

- **MCP server:** `src/retirementops/mcp_servers/service_ops/` (ten tools, stdio and Streamable
  HTTP), `services/{drafting,evidence,policy}.py`, `store/workflow.py`,
  `connectors/servicekb_mock.py`, `db/scoped.py`, `governance/metrics.py`, `scripts/dev_token.py`,
  `.mcp.json.example`.
- **Migrations:** `0002_mcp_service_ops`, `0003_workflow_integrity`,
  `0004_per_user_idempotency_and_guard_hardening`, `0005_pin_tenant_scope_search_path`, plus
  `scripts/db_upgrade.py`.
- **Security fixes in existing code:** redaction of standard Python logs, restricted-pattern
  masking, `hide_parameters` on engines, connector lookups by ID, the SDK pin and import contracts.
- **Tests:** `tests/unit/test_mcp_registry.py`,
  `tests/integration/{test_mcp_tools,test_mcp_transports,test_migrations}.py`,
  `tests/security/{test_m2_review_regressions,test_tenant_scope_hardening}.py`, and extensions to
  four existing test files.
- **Docs:** ADR-013, `docs/mcp-service-ops.md`, `docs/mcp-inspector-transcript.md`,
  `docs/operations-migrations.md`, the five root documents, the documentation rules in
  `CLAUDE.md`, and updates to existing docs.

## What M2 implemented

1. **M2:** the ten MCP tools, stdio and Streamable HTTP, the policy KB mock system, drafts and
   proposals stores, evidence verification, metrics, the Inspector transcript (ADR-013).
2. **First review fixes:** H-1 (database insert guards, migration 0003), M-1 (scope-checked evidence),
   M-2 (bounded metrics), M-3 (SDK pin), Low-1 to Low-9.
3. **Final review fixes:** N-1 (per-user idempotency, scope check before any replay; migration 0004),
   `hide_parameters`, `created_by_kind`, M2-only `NEEDS_MANUAL_REVIEW`, pinned guard search paths,
   `skill_version` in replay, runbook wording.
4. **Final hardening:** migration 0005 pins `sec.tenant_scope()` (`search_path = pg_catalog`,
   qualified references, identical behaviour and grants, exact downgrade), plus the five root
   documents and the documentation rules in `CLAUDE.md`.
5. **Pre-commit audit fixes (documentation only):**
   - personal paths removed from the Inspector transcript;
   - its timestamp and correlation ID redacted;
   - its header quoting corrected;
   - the migration-history statement in `DECISIONS.md` corrected;
   - two stale numbers updated.

## What was tested (actual results, 2026-11-26)

- Full suite: **449 passed, 0 failed** (unit 162, integration 105, security 182), 97% coverage.
  This ran on the reviewed tree; only documentation changed between that run and the commit.
- Ruff, format, mypy, 5 import contracts, lockfile and secret scan all clean.
- Migration round trips (0005↔0004, head→0003→head→0002→0001→head→base), safe in-place upgrade,
  shared-key downgrade: passed.
- Live adversarial re-runs:
  - the N-1 replay exploit is closed;
  - 13 database guard bypass attempts were all blocked;
  - RLS is unaffected by the caller's `search_path`;
  - a shadowing attack widens the scope at 0004 and is defeated at 0005;
  - real database-failure logging through the SDK and HTTP leaks nothing.
- **Before the commit:**
  - the staged set matched the reviewed set exactly (69 files);
  - `git diff --cached --check` was clean;
  - no secrets, credentials, personal paths or identifiers were staged.

Details: [CURRENT_STATUS.md](CURRENT_STATUS.md).

## Local environment facts

- Docker Postgres 16.10 at `127.0.0.1:55432`. The dev database `retirementops` is at migration
  **0005** with seed-42 data plus drafts and proposals created during testing.
- `.env` is generated by `scripts/dev_bootstrap.py`, which appends new keys without overwriting
  values. The dev JWT keys are in `keys/`.
- A Homebrew Postgres also listens on 5432; it is unrelated.

## Outstanding issues

None critical, high or medium in security. See [KNOWN_ISSUES.md](KNOWN_ISSUES.md). The most important
open items are that CI has no verified run on GitHub (OPEN-1) and that Actions are pinned to tags,
not SHAs (OPEN-2).

## M3 progress (2026-11-26, uncommitted)

The owner approved starting M3. Stages 1–5 of 7 and the run-trace storage are implemented and
tested in the working tree (692 passed, 1 opt-in live test skipped); nothing is committed. Details: CURRENT_STATUS.md "Remaining work" and the M3
section of DECISIONS.md. Uncommitted files:

- new: `migrations/versions/0006_validator_pending_review.py`,
  `migrations/versions/0007_agent_run_traces.py`, `src/retirementops/store/traces.py`,
  `src/retirementops/agent_runtime/` (`skills.py`, `providers/{contracts,prompts,scripted,scripts,ollama}.py`),
  `skills/service-ticket-triage/{SKILL,examples}.md`,
  `skills/contribution-investigation/{SKILL,evidence-checklist}.md`,
  `tests/unit/{test_skill_loader,test_providers}.py`, `tests/security/test_m3_agent_recorder.py`,
  `src/retirementops/domain/routing.py`,
  `src/retirementops/domain/draft_validator.py`, `src/retirementops/services/draft_validation.py`,
  `tests/unit/test_routing_engine.py`, `tests/unit/test_draft_validator.py`,
  `tests/unit/test_run_tokens.py`, `tests/security/test_m3_draft_validation.py`,
  `tests/security/test_m3_run_binding.py`;
- changed: `pyproject.toml` (import contracts), `.env.example`, `config.py`, `domain/authz.py`, `governance/tokens.py`, `services/{audit_service,
  business,container,context,drafting,evidence,identity}.py`, `store/workflow.py`,
  `mcp_servers/service_ops/tools.py`, six existing test files (deliberate expectation updates),
  `README.md`, `docs/implementation-plan.md` and the five root documents.

The dev database is still at migration 0005 and was deliberately not upgraded. When approved,
`scripts/db_upgrade.py` (keeps data) creates the `svc_agent_recorder` login and applies 0006–0007.
The local `.env` gained `RO_PW_SVC_AGENT_RECORDER` via `scripts/dev_bootstrap.py`.

## Decisions awaiting approval

1. Whether and when to push `main` to GitHub. No push has been verified; pushing would let CI run
   there (OPEN-1).
2. When to commit the M3 stages, and when to upgrade the dev database (0005 → 0007).
   (Decided 2026-11-26: run traces use the dedicated `svc_agent_recorder` role; see DECISIONS.md.)

## Exact next steps for another session

1. Read `CLAUDE.md`, then this file, `CURRENT_STATUS.md` and `KNOWN_ISSUES.md`.
2. Verify the environment before anything else:

```bash
docker compose up -d --wait db
```

```bash
uv sync --locked && uv run python scripts/dev_bootstrap.py && uv run python scripts/db_upgrade.py
```

```bash
uv run ruff check src tests scripts migrations && uv run ruff format --check src tests scripts migrations && uv run mypy && uv run lint-imports && uv run pytest -q
```

3. Do not commit uncommitted changes, push or fetch without the owner's approval. Do not amend or
   rewrite `ac9e36b`, `91f959b`, `f4d3349` or `3d281b6`.
4. **M3 is in progress (approved 2026-11-26).** Continue from stage 6, following the M3 section of
   [docs/implementation-plan.md](docs/implementation-plan.md) and the M3 obligations in ADR-013:
   - run tokens with minimum scopes;
   - the validator before insert, re-allowing `PENDING_REVIEW` in a new migration that keeps the
     pinned `search_path`;
   - the routing rules engine and a server-side check of proposed queues;
   - the skills.

   Do not widen `app_service` privileges. Update this file after M3.
