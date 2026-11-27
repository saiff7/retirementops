# Current status

_Last updated: 2026-11-27 (M3 complete; M4 Stage 0 approved and committed in `3670c4e`; Stage 1 not started)._

## Milestones

| Milestone | State | Commit |
|---|---|---|
| M0 Discovery and design | Done, approved | `ac9e36b` |
| M1 Synthetic business backend | Done, approved | `ac9e36b` |
| M2 Service Operations MCP server | Done: completed, reviewed three times, approved and committed | `91f959b` |
| M3 First skill and worker | **Complete: all seven stages committed.** Stages 1–5 in `1515ed2`, stage 6 in `d7a9a63`, stage 7 (developer skills) in `3ab1238` | `1515ed2`, `d7a9a63`, `3ab1238` |
| M4 Evaluation suite | **Stage 0 (decisions and ADR amendments, documentation only) approved and committed 2026-11-27.** Stages 1–8 not started | `3670c4e` |
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

## Remaining work

1. Owner decision on pushing `main` to GitHub, which would let CI run there (OPEN-1); see
   [HANDOFF.md](HANDOFF.md). Requires the owner's approval.
2. **M3, in progress** ([plan](docs/implementation-plan.md)). Stages 1–5 committed as `1515ed2`:
   - stage 1: routing rules engine (`domain/routing.py`) and draft validator (`domain/draft_validator.py`);
   - stage 2: migration 0006 re-allows `PENDING_REVIEW` only for a validator-passed draft; every
     draft is validated server-side before insert (`services/draft_validation.py`);
   - stage 3: run-scoped agent tokens bound to one tenant, ticket, subject set and permitted queue;
     services enforce the binding and the routing queue on proposals;
   - run-trace storage: migration 0007 (`app.agent_runs`, `app.tool_calls`, `app.evidence_items`)
     written only by the new `svc_agent_recorder` role (owner decision), plus `store/traces.py`;
   - stage 4: business skills `skills/service-ticket-triage/` and `skills/contribution-investigation/`
     and a strict, content-hashing loader (`agent_runtime/skills.py`);
   - stage 5: the `ModelProvider` contract, prompt construction, `ScriptedProvider` with the M3
     adversarial scripts, and a loopback-only `OllamaProvider` (one live call verified locally).

   - stage 6 (committed): the orchestrator (`agent_runtime/orchestrator.py`) with the run state
     machine, host-side resolution and risk detectors (`services/ticket_resolution.py`,
     `domain/risk_detectors.py`), host checks on every model-proposed call, budgets, a kill switch,
     the in-process MCP client, trace writing as `svc_agent_recorder`, audit, and
     `scripts/run_worker.py`. **Both M3 acceptance criteria pass** (see the results below).

   - stage 7 (committed as `3ab1238`): Claude Code developer skills
     `.claude/skills/{add-connector,add-mcp-tool,write-eval,security-review}/SKILL.md`. Checked:
     frontmatter valid, every cited file and symbol exists, every command they give runs
     (full suite 840 passed, 1 skipped; all checks clean).

   M3 is complete.
3. **M4, Stage 0 approved and committed (2026-11-27, `3670c4e`):**
   - the evaluation design, including the scoring-reader design, is in ADR-010 Amendment 1 and
     ADR-005 Amendment 1, both accepted by the owner;
   - two security reviews of that design, against the code and the live catalog, were documented;
     they added OPEN-11, OPEN-12 and ACC-15 to [KNOWN_ISSUES.md](KNOWN_ISSUES.md);
   - `DECISIONS.md` "M4 decisions" indexes them;
   - the stage plan is in the M4 section of [docs/implementation-plan.md](docs/implementation-plan.md).

   No M4 code, datasets, tests, migrations or CI changes exist yet. Stage 1 needs the owner's
   approval.

## M4 progress (implementation started 2026-11-27, owner-authorised overnight run)

| Stage | State | Evidence (commands actually run) |
|---|---|---|
| 1 Golden dataset | **Done.** 30 seed-based cases (20 dev, 10 held-out), all six categories in both splits; manifest with per-case hashes, seed hashes and tenant list; `scripts/build_golden.py` (independent reference calculation, `--check`); `evaluations/dataset.py`; import contract for `evaluations` | `tests/unit/test_golden_dataset.py` 14 passed; full suite 854 passed, 1 skipped (2026-11-27) |

Open risks and debt: [KNOWN_ISSUES.md](KNOWN_ISSUES.md).
