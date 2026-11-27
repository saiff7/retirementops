# Handoff

_Last updated: 2026-11-27, after the M4 Stage 8 commit `9aa0c95` (overnight autonomous run, owner-authorised)._

## Current state in one paragraph

M0, M1 and M2 are completed, reviewed, approved and committed on `main`. M2 (the Service
Operations MCP server) went through three independent security reviews, with every finding fixed
or explicitly accepted. The owner approved it, and it was committed as `91f959b`. The
documentation recording that was committed separately as `f4d3349` and corrected in `3d281b6`.
GitHub push status is unverified: no push has been verified and no fetch has been performed.
The owner approved starting M3 on 2026-11-26. **M3 is complete: all seven stages are committed**: stages 1–5 in `1515ed2`, stage 6 in `d7a9a63`, stage 7 (developer skills) in `3ab1238`. The owner approved the M4 architectural decisions on 2026-11-27. Stage 0 was approved and committed as `3670c4e`. **M4 Stages 1–8 were implemented, tested and committed locally overnight (2026-11-27) and await the owner's review.** Nothing has been pushed; the new CI step has never run on GitHub.

## Git

| | |
|---|---|
| Branch | `main` (the only branch) |
| Local commits | Oldest first: `ac9e36b`, `91f959b`, `f4d3349`, `3d281b6`, `b37d9c5`, `1515ed2`, `d7a9a63`, `3ab1238`, `a674e35` ("docs: mark M3 fully complete"), `3670c4e`, `b469a13` (M4 Stage 0), `db6b6ca` (Stage 1), `c47170f` (Stage 2), `a484171` (Stage 3), `774b6cf` (Stage 4), `9d22fe2` (Stage 5), `9806087` (Stage 6), `b11a4c3` (Stage 7), `9aa0c95` (Stage 8), then the handoff documentation commit (run `git log` for the latest) |
| M4 Stage 0 commit | `3670c4ec3ed4b348fa1d0c55d8b352b94495911d`, "docs: approve M4 evaluation design" (nine documentation files; no code, tests, configuration, migrations or CI) |
| M3 stage 7 commit | `3ab1238be20bf4123a3f4ba255b0f11037decca9`, "M3 stage 7: add Claude Code developer skills" (9 files) |
| M3 stage 6 commit | `d7a9a6342733c9470db0d828ae05c0804ed4ce99`, "M3 stage 6: orchestrator, host resolution and review fixes" (29 files) |
| Documentation correction commit | `3d281b64f0c35e9c1c1e733bd78cf58f02bc2a00`, "Docs: correct project status and handoff" (six documentation files; no source code, tests or SQL) |
| Documentation commit | `f4d3349de364160a19af6551d53fb9d8ca564648`, "Docs: finalize M2 project handoff" (eight documentation files, including the migration 0005 docstring date; no source code, tests or SQL) |
| M2 commit | `91f959b2110b8210a10efc3ad7e072c8e9cfae8b`, "M2: add hardened service operations MCP server" (69 files, +6,487 / −128) |
| M0 + M1 commit | `ac9e36b47feab8b9a58f1c3c82add8cdc6bbf853`, "M0 + M1: design docs and synthetic business backend" |
| Remote | `origin` = `github.com/saiff7/retirementops`. **Push status unverified:** no push has been verified and no fetch has been performed, so the local repository has no remote-tracking information. Whether GitHub holds any of these commits has not been checked. |
| Working tree | Clean after the M4 commits (2026-11-27). Run `git status` for the current state. |

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
is committed as `3ab1238`. Details: CURRENT_STATUS.md and the M3 sections of DECISIONS.md.

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

## M3 stage 7 (2026-11-27, committed as `3ab1238`)

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

## M4 (2026-11-27, overnight run; committed locally, not pushed)

| Stage | Commit | Result |
|---|---|---|
| 1 Golden dataset | `db6b6ca` | 30 seed-based cases (20 dev, 10 held-out), manifest with hashes and tenant list |
| 2 Eval DB and reader | `c47170f` | `retirementops_eval` (name-guarded), `svc_eval_reader` SELECT-only, `Settings.unprivileged()` |
| 3 Recording, observer, runner | `a484171` | One Orchestrator per case, strict redacted results, full metadata |
| 4 Scorers | `774b6cf` | All spec §12 metrics; M5-dependent ones N/A |
| 5 Safety tier and gates | `9d22fe2` | 104 scripted runs, all 11 gates pass; `scripts/run_eval.py` |
| 6 Quality tier | `9806087` | 8B vs 14B on dev (×3) and held-out (×1); [comparison](docs/model-comparison-m4.md) |
| 7 Reports and baselines | `b11a4c3` | Reproducible reports, baselines, regression flags |
| 8 CI and deliberate break | `9aa0c95` | Blocking CI step (local only), deliberate-break demo shows exit 1 |

Final local results (2026-11-27): full suite **963 passed, 1 skipped** (the opt-in live Ollama
test); ruff, format, mypy, 7 import contracts, golden check and secret scan clean; safety tier all
gates passed. Databases: dev `retirementops` still at **0005** (never touched), test database
rebuilt only by the test suite as always, `retirementops_eval` rebuilt by each evaluation run.
New known items: OPEN-13 (conflicting rules not returned as evidence), ACC-15 (accepted snapshot
blind spot), OPEN-11/12 (from the Stage 0 review).

## Final-review follow-up (2026-11-27, committed after owner review; not pushed)

- **M-3, recorded as it happened:** the overnight instruction said not to modify
  `retirementops_test`, but every `pytest` run during the overnight session rebuilt it (the suite's
  own fixture, `tests/conftest.py`, bootstraps it with `drop_existing=True`). Its database OID
  changed from 193283 to 254440. Nothing else touched it. Since the owner's instruction of
  2026-11-27 it has **not** been touched again: the full suite was not run, only database-free
  unit tests and evaluation tests whose fixtures use `retirementops_eval` alone (checked with
  `pytest --setup-plan`); OIDs before and after: dev 23849, test 254440, unchanged.
- **M-1 and M-2 fixed in the evaluation scorer** (scorer `eval-scorers/2`, golden dataset 1.1.0):
  draft dates are scored against the golden period-end date, and evidence and citations count
  only when the record itself belongs to the case's ticket, participant, plan and pay period.
  Server-side financial and safety enforcement is unchanged.
- **Previous results are not comparable:** the committed reports and baselines were scored with
  `eval-scorers/1` on dataset 1.0.0; they are kept unchanged as history, and every new report says
  its baseline is not comparable. To get scorer-2 numbers, rerun the safety tier and the four
  quality runs, then re-baseline deliberately with `--set-baseline`.

## Release preparation, Stage 1 (2026-11-27)

Re-evaluated under `eval-scorers/2` / dataset 1.1.0 (safety tier plus all four quality runs), all
gates passed, results identical to scorer 1 except latency; scorer-1 baselines archived in
`evals/reports/baselines/archive/` and replaced by the scorer-2 runs. Details: CURRENT_STATUS.md.
Next: Stage 2 (classify every outstanding issue) after the owner's review.

## Decisions awaiting approval

1. Whether and when to push `main` to GitHub. No push has been verified; pushing would let CI run
   there (OPEN-1).
2. When to upgrade the dev database (0005 → 0007).
3. **Reviewing M4** (Stages 1–8, `db6b6ca`…`9aa0c95`): the code, the initial baselines set by the
   overnight run, and the model comparison. History of the Stage 0 text, all on 2026-11-27:
   - **Scoring-access decision revised** at the owner's request: `ro_owner` is for eval setup only,
     and scoring reads use a separate SELECT-only `svc_eval_reader` (ADR-010 Amendment 1, point 1a).
   - **First security review**, against the code and live catalog:
     - corrected the setup identities: the superuser creates the database, and `ro_owner`
       migrates, seeds and grants;
     - required process separation for privileged secrets;
     - recorded OPEN-11 and OPEN-12.
   - **Final review:**
     - assigned each boundary test to its stage (Stage 2 or Stage 3);
     - set all approval wording to "pending" until the owner approved it (updated to "accepted"
       after approval);
     - listed the entry points that must check the privileged secrets;
     - named the golden manifest's tenant list as the reader's tenant scope;
     - extended the planned CLAUDE.md note to three exceptions;
     - recorded the accepted snapshot blind spot (ACC-15) and the cluster-wide role refresh.
4. The default model after the M4 Stage 6 comparison: the recommendation is to keep `qwen3:8b`
   ([docs/model-comparison-m4.md](docs/model-comparison-m4.md)); ADR-005 Amendment 1.
5. Extending the credential-separation fix (OPEN-11) beyond the evaluation process (API, MCP
   server, worker): needs separate owner approval.
   (Decided 2026-11-26: run traces use the dedicated `svc_agent_recorder` role. Decided
   2026-11-27: the M4 architectural decisions and the Stage 0 text; no `ro_owner` for scoring; the snapshot blind spot
   accepted; the OPEN-11 fix limited to the evaluation process. See DECISIONS.md.)

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
4. **M4 is implemented locally and awaits the owner's review.** Exact next action: the owner
   reviews M4 and decides on pushing `main` so GitHub CI can run the new safety step (M4
   acceptance, OPEN-1). Demonstrate with `uv run python scripts/run_eval.py --tier safety` and
   `uv run python scripts/demo_deliberate_break.py`. Do not start M5 without approval.
   When approved, follow the M4 stage table in
   [docs/implementation-plan.md](docs/implementation-plan.md), ADR-010 Amendment 1 and the
   `write-eval` developer skill. Do not widen `app_service` privileges.
