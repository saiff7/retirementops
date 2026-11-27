# Current status

_Last updated: 2026-11-27 (M4 Stages 1–8 implemented and committed locally; awaiting owner review; never pushed)._

## Milestones

| Milestone | State | Commit |
|---|---|---|
| M0 Discovery and design | Done, approved | `ac9e36b` |
| M1 Synthetic business backend | Done, approved | `ac9e36b` |
| M2 Service Operations MCP server | Done: completed, reviewed three times, approved and committed | `91f959b` |
| M3 First skill and worker | **Complete: all seven stages committed.** Stages 1–5 in `1515ed2`, stage 6 in `d7a9a63`, stage 7 (developer skills) in `3ab1238` | `1515ed2`, `d7a9a63`, `3ab1238` |
| M4 Evaluation suite | **Stages 0–8 implemented and committed locally (2026-11-27); awaiting owner review.** Acceptance: deliberate-break shown locally; local Ollama reports committed; GitHub CI not run (needs a push, OPEN-1) | `3670c4e` … see the M4 progress table |
| M5–M8 | Not started | — |

## Repository state

_As recorded on 2026-11-26. Run `git log` and `git status` for the current state._

- Branch `main`. Local commits as of this update, oldest first (run `git log` for any later ones):
  - `ac9e36b47feab8b9a58f1c3c82add8cdc6bbf853`, "M0 + M1: design docs and synthetic business backend".
  - `91f959b2110b8210a10efc3ad7e072c8e9cfae8b`, "M2: add hardened service operations MCP server"
    (69 files).
  - `f4d3349de364160a19af6551d53fb9d8ca564648`, "Docs: finalize M2 project handoff". It records the
    M2 commit in eight documentation files, including the migration 0005 docstring date; no source
    code, tests or SQL changed.
  - `3d281b64f0c35e9c1c1e733bd78cf58f02bc2a00`, "Docs: correct project status and handoff". It
    corrects the M2, M3 and GitHub status wording in six documentation files; no source code, tests
    or SQL changed.
  - `b37d9c5b105bec5d7e1d4fdffb4fc1ca7dd26aae`, "Docs: record documentation commit 3d281b6".
  - `1515ed28239ed0d81287acf16a2cb48cb9e47a38`, "M3 stages 1-5: validator, run tokens, trace
    storage, skills, providers" (52 files).
  - `d7a9a6342733c9470db0d828ae05c0804ed4ce99`, "M3 stage 6: orchestrator, host resolution and review fixes" (29 files).
  - `3ab1238be20bf4123a3f4ba255b0f11037decca9`, "M3 stage 7: add Claude Code developer skills" (9 files).
  - `a674e351459c3e04391face023d3b5a0ef971b2d`, "docs: mark M3 fully complete".
  - `3670c4ec3ed4b348fa1d0c55d8b352b94495911d`, "docs: approve M4 evaluation design" (nine
    documentation files; no code, tests, configuration, migrations or CI). A follow-up
    documentation commit records the approval in the status wording.
- **GitHub push status is unverified.** `origin` is configured, but no push has been verified and no
  fetch has been performed, so the local repository has no remote-tracking information about
  GitHub. Whether GitHub holds any of these commits has not been checked.

## M2 implementation status (committed)

- **Delivered:**
  - the ten spec §6 tools over stdio and Streamable HTTP;
  - scope-filtered discovery, Pydantic `extra="forbid"` inputs, audited denials, metrics;
  - drafts and proposals as rows only, with server-side evidence verification;
  - the [Inspector transcript](docs/mcp-inspector-transcript.md).
- **Review fixes:** H-1, M-1 to M-3, Low-1 to Low-9, N-1 and its lows, and the final
  `sec.tenant_scope()` pin. All are listed in [ADR-013](docs/adr/013-mcp-server-implementation.md).
- **Database:** migrations 0001–0005. The dev database (`retirementops`) is upgraded in place to
  **0005** with its data kept. The test database is rebuilt on every test run.

## Latest verified results (2026-11-26, the reviewed tree committed as `91f959b`; only documentation changed after the test run)

| Check | Result |
|---|---|
| Full test suite | **449 passed, 0 failed** (unit 162, integration 105, security 182) |
| Coverage | **97%** (3,006 statements, 96 missed) |
| Migration round trip, safe upgrade, shared-key downgrade | 3 passed |
| `sec.tenant_scope()` hardening (pin, unchanged behaviour, shadowing, roles) | 33 passed |
| Runtime roles and RLS | 84 passed |
| Idempotency and review regressions | 29 passed |
| M1 API, abuse protection and audit privacy | 47 passed |
| MCP tools, transports and registry | 107 passed |
| Ruff lint and format | Clean (99 files formatted) |
| mypy | Clean (61 source files) |
| Import contracts | 5 kept, 0 broken |
| Lockfile | Consistent |
| Secret scan | Passed (141 tracked files); no local secret values or JWTs in changed files |
| Live adversarial re-runs | N-1 replay closed; 13 database guard bypass attempts as `app_service` all blocked; RLS unaffected by the caller's `search_path`; real database-failure logging (SDK and HTTP) leaks nothing |

CI has **no verified run** on GitHub: no push has been verified and no CI result has been observed.

### M3 results (2026-11-26/27; stages 1–5 in `1515ed2`, stage 6 in the stage 6 commit)

| Check | Result |
|---|---|
| Full test suite | **840 passed, 0 failed, 1 skipped** (the opt-in live Ollama test), after both stage 6 reviews' fixes |
| Second review fixes | Whole-run non-blocking lock (three genuine two-thread tests, shown to fail without it); validator `draft-validator/2` (money/record promises only, dedicated affirmative-only instruction detector; 21 malicious examples caught, 11 legitimate replies pass) with a rules-fingerprint pin |
| Stage 6 review fixes | Operator-only runs (refused before any model call), read-only evidence token plus 120 s host write tokens, validator rules for links/emails/phones/promises/instruction text, skill `1.1.0` with content pins, audited trace failures, reentrancy and malformed-argument guards: 12 end-to-end regression tests plus unit tests |
| M3 acceptance 1 | Seeded discrepancy ticket (ZD-1005, one-cent shortfall) runs RECEIVED → … → PENDING_REVIEW with the correct finding, a validated draft, a routing proposal and a stored trace |
| M3 acceptance 2–3 | Invented amount, cross-tenant call, unlisted tool, close-ticket request, malformed JSON and timeout each end in NEEDS_MANUAL_REVIEW; mock-system snapshots unchanged for every run |
| Seed sweep | Every seeded contribution ticket reaches its labelled outcome (state, queue, finding) with the scripted model |
| Live local model | `qwen3:8b` on ZD-1005 against the test database: 3 of 3 runs reached PENDING_REVIEW with validator-passed drafts, before and after the review fixes (a smoke check, not an evaluation) |
| Live Ollama check | `RO_OLLAMA_LIVE=1`: 1 passed with `qwen3:8b` (schema-valid classification, digest recorded) |
| Ruff lint and format, mypy, import contracts | Clean; 6 contracts kept (new: agent runtime never touches connectors, store or DB) |
| Migration round trips | Pass, including 0006 ↔ 0005 and 0007 ↔ 0006 (grants revoked on the way down) |
| Recorder role, checked in the migrated test DB | INSERT/SELECT on the three trace tables only; no other role can touch them; `app_service` grants unchanged |
| Dev database | Still at **0005**, not upgraded (owner instruction). `scripts/db_upgrade.py` would bootstrap the new role and apply 0006–0007 |
| Local `.env` | `scripts/dev_bootstrap.py` appended `RO_PW_SVC_AGENT_RECORDER` (git-ignored; no other value changed) |

## Final-review fixes (2026-11-27, committed after owner review)

| Fix | What changed | Evidence (commands actually run) |
|---|---|---|
| M-1 date factuality | Golden dataset **1.1.0** adds `subject.period_end` (only that field; every other expectation byte-identical); the runner normalises ISO, month-name and m/d/yyyy dates; the scorer flags `UNSUPPORTED_DATE` for any date other than the case's period end | `tests/unit/test_golden_dataset.py`, `test_m4_scorers.py`, `test_m4_reports.py`: 56 passed |
| M-2 evidence ownership | The observer reads each cited or retrieved record's own tenant, participant, plan and pay period; citations and coverage count only owned records (payroll rows by participant, plan and period; policy snippets only if the provider-wide snippet exists); scorer **`eval-scorers/2`**, recorded in run metadata | Same unit tests, plus `tests/evals` without the two tests that need the test database: 35 passed, 2 deselected; safety tier (no report written): all gates passed, 104 runs |
| Baselines | Compared only when scorer and dataset versions match; otherwise reported as "not comparable"; baseline files untouched | Unit test checks the committed baseline file is byte-identical after use |

**Previous M4 results** (committed reports and baselines) were scored with `eval-scorers/1` on
dataset 1.0.0. They remain valid as history but must be rerun for scorer-2 numbers. The full
suite was **not** run after these changes (it would rebuild `retirementops_test`).

## Remaining work

1. **Owner review of M4** (Stages 1–8, committed locally on 2026-11-27): code, initial baselines,
   the three extra blocking gates, and the model comparison (recommendation: keep `qwen3:8b`).
2. Owner decision on pushing `main` to GitHub so CI can run, including the new blocking safety step
   (M4 acceptance; OPEN-1). Nothing has been pushed.
3. Not required by M4: the dev database upgrade 0005 → 0007; M5 (not started).

## M4 progress (implementation started 2026-11-27, owner-authorised overnight run)

| Stage | State | Evidence (commands actually run) |
|---|---|---|
| 1 Golden dataset | **Done.** 30 seed-based cases (20 dev, 10 held-out), all six categories in both splits; manifest with per-case hashes, seed hashes and tenant list; `scripts/build_golden.py` (independent reference calculation, `--check`); `evaluations/dataset.py`; import contract for `evaluations` | `tests/unit/test_golden_dataset.py` 14 passed; full suite 854 passed, 1 skipped (2026-11-27) |
| 2 Eval database and reader | **Done.** `evaluations/provision.py` (name guard; superuser cluster step; `ro_owner` database step; fixed reader allowlist); `svc_eval_reader` role (not a runtime role); `Settings.unprivileged()` and `MissingSecret` for the run-and-score process; CLAUDE.md note on the three exceptions. `retirementops_eval` provisioned locally (dev database still at 0005, test database untouched by provisioning) | `tests/security/test_m4_eval_provisioning.py` 46 passed; full suite 900 passed, 1 skipped (2026-11-27). Not done: a live "sabotage" grant to prove the privilege tests fail (refused: it would give the reader write rights); the tests assert exact equality instead |
| 3 Recording, observer, runner | **Done.** `evaluations/recording.py` (provider wrapper: first-pass choices, proposed arguments, parse failures incl. silent retries, latency); `evaluations/observe.py` (read-only, as `svc_eval_reader`, mock-system snapshots and readback); `evaluations/runner.py` (one Orchestrator per case, strict `CaseResult` without ticket/draft text, full run metadata, eval rate limits). All 30 golden cases reach their expected state, queue, finding and reason with the faithful scripted model | `tests/evals/test_m4_runner.py` 11 passed; full suite 911 passed, 1 skipped (2026-11-27). `scripts/run_eval.py` lands with Stage 5 (it needs the gates for its exit code) |
| 4 Deterministic scorers | **Done.** `evaluations/scorers.py`: intent accuracy and macro-F1, routing accuracy, manual-review recall and precision, evidence coverage, citation precision and ownership, unsupported-claim rate (amounts, identifiers, action claims against the golden oracle), parse-failure rate and accepted-output schema validity, first-pass tool precision, recall and argument accuracy, host-feedback rate, denied attempts, cross-tenant leaks, injection resistance, duplicate open proposals, mock-system writes, missing traces, p50/p95 latency, per-case pass/fail; review-queue age and approval/rejection rates reported as "not applicable until M5" | `tests/unit/test_m4_scorers.py` 18 passed (incl. a macro-F1 property test); full suite 929 passed, 1 skipped (2026-11-27) |
| 5 Safety tier and gates | **Done.** `evaluations/gates.py` (the eight ADR-010 gates as defined in Amendment 1, plus traces written, adversarial runs fail closed, faithful model reproduces golden), `evaluations/safety.py` (30 faithful runs, 60-run adversarial matrix: the six M3 scripts plus echo-injection, self-approval draft, write tool as model, oversized arguments and money-promise draft; 14-run misclassification probe), `evaluations/cli.py` + `scripts/run_eval.py` (provisioning child process first, then unprivileged run-and-score; exit 1 on any gate failure), `evaluations/report.py` (redacted reports). Local run: 104 runs, **all 11 gates passed**, injection 8/8, adversarial 60/60 failed closed | `tests/evals/test_m4_safety_gates.py` 26 passed (incl. live sabotage: disabled validator trips the financial gate and makes the CLI exit 1; broken trace store trips the trace gate; removed host allowlist is still caught by the server); full suite 955 passed, 1 skipped (2026-11-27) |
| 6 Quality tier, 8B vs 14B | **Done.** Dev (20 cases × 3 repeats) then held-out (10 × 1) for both models; reports committed under `evals/reports/`; write-up in [docs/model-comparison-m4.md](docs/model-comparison-m4.md). Dev: 8B macro-F1 1.00, routing 1.00, case pass 0.95; 14B macro-F1 0.97, routing 1.00, case pass 0.90. Held-out: both 1.00 / 1.00, case pass 1.00. All safety gates passed for both. Correctness metrics had zero variance across repeats. **Default stays `qwen3:8b`** (recommendation; owner decides) | `scripts/run_eval.py --tier quality` runs on 2026-11-27: 8B dev 8 min 33 s, 14B dev 15 min 36 s, held-out about 1–2 min each |
| 7 Reports, baselines, regressions | **Done.** `evaluations/report.py` (metadata, redacted results, summary, Markdown; a leaking file is deleted and the run fails) and `evaluations/baseline.py` (flags any drop in a correctness metric, latency rises above 25%, pass→fail cases and gates); initial baselines in `evals/reports/baselines/` for the safety tier and all four quality runs (set by this run with `--set-baseline`; **owner review pending**). Two consecutive safety runs (104 runs each) are identical apart from timing; the second shows 0 regressions against the baseline | `tests/unit/test_m4_reports.py` 8 passed (byte-identical rendering, schema, redaction, regression kinds, latency tolerance) |
| 8 CI integration and deliberate break | **Done locally; never run on GitHub.** `.github/workflows/ci.yml` gains a golden `--check` step and a blocking `run_eval.py --tier safety` step (no artifact upload); `scripts/demo_deliberate_break.py` applies `evals/deliberate-break/disable-unsupported-amount-check.patch` in a temporary worktree and shows the safety tier exit 1 with `unsupported_financial_values_accepted` and `adversarial_runs_fail_closed` failed; `docs/evaluation-plan.md` | Every CI step run locally in order on 2026-11-27: secret scan, `uv sync --locked`, ruff, format, mypy, 7 contracts, golden check, pytest **963 passed, 1 skipped**, safety tier all gates passed (exit 0). Demo: broken build exit 1, demonstrated. **No push, so no GitHub CI result (OPEN-1)** |

Open risks and debt: [KNOWN_ISSUES.md](KNOWN_ISSUES.md).
