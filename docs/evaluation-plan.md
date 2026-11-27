# Evaluation suite (M4)

SYNTHETIC DATA ONLY. Design: [ADR-010 Amendment 1](adr/010-evaluation-and-ci-gates.md) and
[ADR-005 Amendment 1](adr/005-explicit-orchestrator-and-providers.md). This page is the how-to.

## Quick start

```bash
docker compose up -d --wait db
```

```bash
uv run python scripts/run_eval.py --tier safety
```

```bash
uv run python scripts/run_eval.py --tier quality --model qwen3:8b --split dev --repeats 3
```

```bash
uv run python scripts/demo_deliberate_break.py
```

Every run first rebuilds `retirementops_eval` in a child process (`python -m
retirementops.evaluations.provision`), then runs and scores the cases in a process that holds no
superuser or `ro_owner` secret. The dev database `retirementops` and the test database
`retirementops_test` are never touched; any other database name is refused.

## Pieces

| Piece | Where | Notes |
|---|---|---|
| Golden cases | `data/golden/{dev,heldout}/cases.jsonl`, `data/golden/manifest.json` | 30 seed-based cases (20 dev, 10 held-out), six categories in both splits. Rebuild and check with `scripts/build_golden.py [--check]`; a change needs a version bump and a new pin in `tests/unit/test_golden_dataset.py`. |
| Provisioning | `src/retirementops/evaluations/provision.py` | Superuser cluster step, then `ro_owner` database step; grants the SELECT-only `svc_eval_reader`. |
| Observation | `evaluations/observe.py` | Read-only, as `svc_eval_reader`, under RLS with the manifest's tenant list. |
| Recording | `evaluations/recording.py` | Wraps the provider: first-pass tool choices, proposed arguments, parse failures, latency. |
| Runner | `evaluations/runner.py` | One `Orchestrator` per case; strict `CaseResult` without ticket or draft text; full run metadata. |
| Scorers | `evaluations/scorers.py` | Spec §12 metrics against frozen expectations only. |
| Gates | `evaluations/gates.py` | ADR-010 safety gates (block) and quality targets (reported). |
| Safety tier | `evaluations/safety.py` | Faithful scripted runs, adversarial matrix, misclassification probe. |
| Quality tier | `evaluations/quality.py` | Local Ollama, repeats, variance. |
| Reports | `evaluations/report.py`, `evals/reports/<run-id>/` | `metadata.json`, `results.jsonl`, `summary.json`, `report.md`; redacted. |
| Baselines | `evaluations/baseline.py`, `evals/reports/baselines/` | Set with `--set-baseline <run-dir>` (a reviewed step); regressions are flagged in every report. |

## Tiers

| Tier | Provider | Blocks? | Where |
|---|---|---|---|
| Safety | `ScriptedProvider` | Yes: `run_eval.py` exits 1 on any gate failure | CI (`.github/workflows/ci.yml`) and locally |
| Quality | `OllamaProvider` (loopback) | No: reported, never relaxes a safety gate | Locally only |

Prompts and skills may be tuned against **dev** only. **Held-out** is run once per model.

## Reading a report

`report.md` starts with the complete run metadata, then the gates, quality targets (met or
**NOT MET**), metrics, variance across repeats, the adversarial matrix, the baseline comparison and
a per-case table. Participant and ticket references are replaced by `{subject.participant}`,
`{other.participant}`, `{subject.ticket}` and `{other.ticket}`. Review-queue age and
approval/rejection rates show "not applicable until M5".

## Known limits

- Local models are not strictly deterministic even at temperature 0 with a fixed seed; quality runs
  use repeats and report the variance.
- The scoring reader cannot see rows of tenants outside its scope list (ACC-15).
- For RULE_CONFLICT cases the tools do not return the conflicting rules as evidence (OPEN-13).
- CI has never run on GitHub (OPEN-1); the CI step is prepared locally only.
