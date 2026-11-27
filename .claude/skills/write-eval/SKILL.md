---
name: write-eval
description: Write a safety-invariant test or a labelled evaluation case for the RetirementOps AI worker, keeping safety gates (scripted provider, CI) separate from model-quality measurement (local model). Use when adding a regression for agent behaviour or a golden case. Developer workflow for Claude Code, not a runtime agent skill.
---

# Write an eval (RetirementOps)

ADR-010 defines two tiers, and they never mix: **safety invariants** run with `ScriptedProvider`
on every test run and any failure blocks; **model quality** runs against a local model and is
reported, never used to relax a safety gate (owner decision).

**What exists today (M3):** pytest safety tests and the scripted provider. **Not yet built (M4):**
the eval runner, `data/golden/{dev,heldout}/` case files, scorers and `evals/reports/`. Do not
create those as a side effect; building them is M4 work and needs the owner's approval.

## Safety invariant (do this for every agent behaviour you add or fix)

1. Describe the hostile model behaviour as a `Script` (see
   `agent_runtime/providers/scripts.py`): wrong tool, reference outside the resolution, invented
   amount, malformed JSON (raw text is parsed like real output), timeout (raise
   `ProviderTimeout`), instruction text in the draft. Register reusable ones in `ADVERSARIAL`, and
   add each new name's expected reason to `expected_reason` in
   `test_adversarial_runs_end_in_manual_review_without_writes`, or that test fails with `KeyError`.
2. Run it through the real `Orchestrator`, services, MCP server and database (pattern:
   `tests/integration/test_m3_orchestrator.py`). Do not mock the boundary you are testing; mock
   only an external failure (for example the trace store raising).
3. Assert the invariant, not the wording: final state `NEEDS_MANUAL_REVIEW`, the reason code,
   no `PENDING_REVIEW` draft, a denial in the trace, and **no mock-system write** (use the
   `no_connector_writes` snapshot fixture, defined in `tests/integration/test_m3_orchestrator.py`,
   not in `conftest.py`, so it is available only in that file). For tenancy, assert `not_found`,
   never data.
4. Money: expected values come from `domain/contributions.py` and `format_money`, never typed by
   hand into a draft fixture unless the test is about a wrong value.

## Labelled case (model quality, local only)

- Build from the seed's ground truth (`seed/generator.py` `ticket_labels`: intent, queue,
  manual-review flag, finding). Never label from the model's own output.
- Keep dev and held-out cases separate; never tune prompts or skills on held-out cases.
- Record provider, model, digest, skill version and hash, validator version and seed with every
  result (the run trace already records most of these).
- Live checks are opt-in only (`RO_OLLAMA_LIVE=1`), never part of the default suite. The default
  model stays `qwen3:8b` unless the owner approves a change.

## Verify

```bash
uv run pytest -q tests/integration/test_m3_orchestrator.py tests/security
```

```bash
uv run ruff check src tests scripts migrations && uv run ruff format --check src tests scripts migrations && uv run mypy && uv run lint-imports && uv run pytest -q
```

Report actual counts. A safety test that only passes because something was mocked away does not
count.
