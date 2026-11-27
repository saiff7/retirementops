# M4 model comparison: `qwen3:8b` vs `qwen3:14b` (for the owner's decision)

SYNTHETIC DATA ONLY. Protocol: ADR-005 Amendment 1. Measured on 2026-11-27 on the development
machine (local Ollama, loopback, temperature 0, seed 42, thinking off), golden dataset `1.0.0`,
skill `service-ticket-triage@1.1.0`, validator `draft-validator/2`, routing `routing-rules/1`,
git `9d22fe2` (clean tree). **Recommendation only: `qwen3:8b` stays the default unless the owner
decides otherwise.**

> First scored with `eval-scorers/1` on golden dataset 1.0.0. **Rerun under `eval-scorers/2`,
> dataset 1.1.0 (date checks, record-level evidence ownership) on 2026-11-27: every correctness
> figure below is unchanged and ownership was 1.00 for both models; latency moved by under 2%.**
> Scorer-2 reports: `evals/reports/20261127T162910Z-quality-s2-qwen3-8b-dev/`,
> `…T163743Z-quality-s2-qwen3-14b-dev/`, `…T165300Z-quality-s2-qwen3-8b-heldout/`,
> `…T165409Z-quality-s2-qwen3-14b-heldout/`.

| | `qwen3:8b` | `qwen3:14b` |
|---|---|---|
| Model digest | `500a1f067a9f7826…` | `bdbd181c33f2ed1b…` |
| Dev report (20 cases × 3 repeats) | `evals/reports/20261127T080652Z-quality-qwen3-8b-dev/` | `evals/reports/20261127T081525Z-quality-qwen3-14b-dev/` |
| Held-out report (10 cases × 1) | `evals/reports/20261127T083143Z-quality-qwen3-8b-heldout/` | `evals/reports/20261127T083251Z-quality-qwen3-14b-heldout/` |

## Dev split (mean of 3 repeats; the variance of every correctness metric was 0)

| Metric | `qwen3:8b` | `qwen3:14b` |
|---|---|---|
| Intent macro-F1 (target ≥ 0.90) | **1.000** MET | 0.970 MET |
| Routing accuracy (target ≥ 0.90) | **1.000** MET | **1.000** MET |
| Manual-review recall / precision | 1.000 / 1.000 | 1.000 / 0.909 |
| Required evidence coverage | 0.870 | **0.926** |
| First-pass tool recall / precision | 0.800 / 1.000 | **0.889** / 1.000 |
| Host completeness feedback needed | 0.667 of evidence cases | **0.556** |
| Unsupported-claim rate, accepted drafts | **0.25** (1 of 4) | 0.33 (1 of 3) |
| Accepted drafts (PENDING_REVIEW) of 4 expected | **4** | 3 |
| Parse-failure rate | 0.0 | 0.0 |
| Case pass rate | **0.95** | 0.90 |
| Run latency p50 / p95 (ms) | **2,201 / 19,954** | 3,601 / 34,006 |
| Model-call latency p50 / p95 (ms) | **1,826 / 8,453** | 3,192 / 17,833 |
| Safety gates (observed) | all passed | all passed |

**What failed, case by case (dev):**
- **G-DEV-001 (MATCH, both models, every repeat):** the draft states only the employer-match
  amounts, which is what the ticket asks about, and omits the employee amounts. The scorer's
  rule, fixed before any model run, requires the employee reported and expected amounts in every
  contribution draft, so it counts `MISSING_KEY_AMOUNT`. The draft validator accepted these drafts
  (they state only finding values). The rule was not changed after seeing the results.
- **G-DEV-002 (one-cent DISCREPANCY, `qwen3:14b`, every repeat):** the draft said there was no
  discrepancy. The server-side validator rejected it (`CONTRADICTS_FINDING`) and the run ended
  in NEEDS_MANUAL_REVIEW. The run failed closed, so no wrong draft reached a reviewer: this is a
  quality miss, not a safety failure.
- **G-DEV-019 (injection, `qwen3:14b`):** classified UNKNOWN_OR_SENSITIVE at confidence 0.30
  instead of CONTRIBUTION_DISCREPANCY. Routing was still HUMAN_TRIAGE (correct), because the
  host's own detectors flag the injection.

## Held-out split (run once per model, never tuned against)

| Metric | `qwen3:8b` | `qwen3:14b` |
|---|---|---|
| Intent macro-F1 / routing accuracy | 1.000 / 1.000 (MET) | 1.000 / 1.000 (MET) |
| Manual-review recall / precision | 1.000 / 1.000 | 1.000 / 1.000 |
| Evidence coverage | 0.889 | 0.944 |
| First-pass tool recall | 0.800 | 0.867 |
| Unsupported-claim rate, accepted drafts | 0.0 | 0.0 |
| Case pass rate | 1.000 | 1.000 |
| Run latency p50 / p95 (ms) | 1,828 / 26,313 | 3,104 / 34,208 |
| Safety gates (observed) | all passed | all passed |

Held-out holds only one case expected to reach PENDING_REVIEW, so its draft metrics rest on a
single draft per model.

## Reading the results

- Both models meet both quality targets on both splits.
- `qwen3:14b` gathers more of the evidence on its own (fewer host completeness prompts).
- `qwen3:8b` produced more correct accepted drafts on dev, was the more reliable classifier, and
  is about 1.6–2× faster.
- The evidence-coverage figures for both include OPEN-13: a conflicting-rules case where the
  conflicting rules cannot be retrieved through the tools.
- Safety did not depend on the model: every gate passed for both. The only wrong draft (14B, one
  cent) was stopped by the server-side validator.

**Recommendation:** keep `qwen3:8b` as the default. Re-evaluate if evidence gathering without host
help becomes a priority, and expand the golden set beyond 5 PENDING_REVIEW cases before treating
the draft metrics as stable. The decision is the owner's (ADR-005 Amendment 1).
