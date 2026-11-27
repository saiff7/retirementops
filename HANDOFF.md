# Handoff

_Last updated: 2026-11-27, after implementing M3 stage 7 (uncommitted)._

## Current state in one paragraph

M0, M1 and M2 are completed, reviewed, approved and committed on `main`. M2 (the Service
Operations MCP server) went through three independent security reviews, with every finding fixed
or explicitly accepted. The owner approved it, and it was committed as `91f959b`. The
documentation recording that was committed separately as `f4d3349` and corrected in `3d281b6`.
GitHub push status is unverified: no push has been verified and no fetch has been performed.
The owner approved starting M3 on 2026-11-26. **All seven M3 stages are implemented**: stages 1–5 in `1515ed2`, stage 6 in `d7a9a63`, and stage 7 (developer skills) in the working tree, not yet committed. M3 awaits owner review and approval. M4 has not started.

## Git

| | |
|---|---|
| Branch | `main` (the only branch) |
| Local commits | Oldest first: `ac9e36b`, `91f959b`, `f4d3349`, `3d281b6`, `b37d9c5`, `1515ed2`, `d7a9a63` (HEAD as of 2026-11-27; run `git log` for any later ones) |
| M3 stage 6 commit | `d7a9a6342733c9470db0d828ae05c0804ed4ce99`, "M3 stage 6: orchestrator, host resolution and review fixes" (29 files) |
| Documentation correction commit | `3d281b64f0c35e9c1c1e733bd78cf58f02bc2a00`, "Docs: correct project status and handoff" (six documentation files; no source code, tests or SQL) |
| Documentation commit | `f4d3349de364160a19af6551d53fb9d8ca564648`, "Docs: finalize M2 project handoff" (eight documentation files, including the migration 0005 docstring date; no source code, tests or SQL) |
| M2 commit | `91f959b2110b8210a10efc3ad7e072c8e9cfae8b`, "M2: add hardened service operations MCP server" (69 files, +6,487 / −128) |
| M0 + M1 commit | `ac9e36b47feab8b9a58f1c3c82add8cdc6bbf853`, "M0 + M1: design docs and synthetic business backend" |
| Remote | `origin` = `github.com/saiff7/retirementops`. **Push status unverified:** no push has been verified and no fetch has been performed, so the local repository has no remote-tracking information. Whether GitHub holds any of these commits has not been checked. |
| Working tree | As of 2026-11-27: stage 7 is uncommitted (four new files under `.claude/skills/` plus updates to the five root documents). Run `git status` for the current state. |

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

## M3 progress (2026-11-26)

The owner approved starting M3. Stages 1–5 and the run-trace storage are committed as
`1515ed2` ("M3 stages 1-5: validator, run tokens, trace storage, skills, providers"). Stage 6, the
orchestrator, was implemented, reviewed twice, fixed and committed as "M3 stage 6: orchestrator, host resolution and review fixes"
(840 passed, 1 opt-in live test skipped, at commit time). Both M3 acceptance criteria pass. Both stage 6 reviews'
important findings and safeguards are fixed (DECISIONS.md "Stage 6 review fixes"); the remaining
minor findings are OPEN-9 and OPEN-10. New drafts record `draft-validator/2`. Stage 7 (developer skills)
has not started. Details: CURRENT_STATUS.md and the M3 sections of DECISIONS.md.

Stage 6 commit contents (29 files):

- new: `src/retirementops/agent_runtime/{orchestrator,mcp_client}.py`,
  `tests/security/test_m3_stage6_review.py`,
  `src/retirementops/domain/{run_state,risk_detectors}.py`,
  `src/retirementops/services/{ticket_resolution,run_traces}.py`, `scripts/run_worker.py`,
  `tests/integration/test_m3_orchestrator.py`, `tests/unit/test_run_state_and_detectors.py`;
- changed (review fixes): `services/identity.py` (operator check, write-token lifetime),
  `governance/tokens.py`, `domain/draft_validator.py` (content rules),
  `skills/service-ticket-triage/SKILL.md` (version `1.1.0`), and tests
  `tests/unit/{test_run_tokens,test_draft_validator,test_skill_loader,test_providers}.py`;
- changed: `config.py` (kill switch), `connectors/{interfaces,planadmin_mock,payroll_mock}.py`
  (`list_plans`, `list_pay_periods`), `services/container.py`, `agent_runtime/providers/prompts.py`,
  `skills/service-ticket-triage/SKILL.md` (one sentence on `missing_evidence`, so the skill hash
  changes), `tests/integration/test_mcp_tools.py` (made independent of test order, OPEN-4), and
  the four root documents.

The dev database is still at migration 0005 and was deliberately not upgraded. The worker was run
against the migrated **test** database (`RO_DB_NAME=retirementops_test`). When approved,
`scripts/db_upgrade.py` (keeps data) creates the `svc_agent_recorder` login and applies 0006–0007.

## M3 stage 7 (2026-11-27, uncommitted)

Four Claude Code developer skills (not runtime agent skills), each a `SKILL.md` with `name` and
`description` frontmatter:

- `.claude/skills/add-connector/`: new mock-system connector, role, RLS, migration and tests;
- `.claude/skills/add-mcp-tool/`: new MCP tool, scopes, run binding, registry and safety tests;
- `.claude/skills/write-eval/`: safety-invariant tests (scripted provider) vs labelled
  model-quality cases; states that the M4 runner, golden files and reports do not exist yet;
- `.claude/skills/security-review/`: the RetirementOps review checklist and evidence rules.

They reference `CLAUDE.md` instead of repeating it. Checks run 2026-11-27: frontmatter valid,
every cited file and symbol exists, the commands they give run (full suite 840 passed, 1 skipped;
ruff, format, mypy, 6 import contracts, secret scan and diff check clean).

## Decisions awaiting approval

1. Whether and when to push `main` to GitHub. No push has been verified; pushing would let CI run
   there (OPEN-1).
2. Review and commit M3 stage 7, then approve M3 as a whole.
3. When to upgrade the dev database (0005 → 0007).
4. The small model comparison ADR-005 asks for (`qwen3:8b` stays the default until then), and
   when to start M4.
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
4. **M3 is implemented and awaits owner approval.** Do not start M4 until the owner says so; when
   approved, follow the M4 section of [docs/implementation-plan.md](docs/implementation-plan.md)
   and the `write-eval` developer skill. Do not widen `app_service` privileges.
