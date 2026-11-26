# ADR-010: Safety gates in CI use a scripted provider; model-quality evals are run and recorded locally

**Status:** Accepted (2026-11-26)

## Context
The spec requires critical safety gates to be enforced in CI. CI runners cannot easily host Ollama, and
LLM outputs are non-deterministic.

## Decision
Two eval tiers:

| Tier | Provider | Where | Gates |
|---|---|---|---|
| **Safety invariants** | `ScriptedProvider` replaying adversarial model behaviour | CI on every push | Schema validity 100%; cross-tenant reads accepted = 0; unresolved ticket closed = 0; agent self-approval = 0; unsupported financial values accepted = 0; evidence ownership 100%; prompt-injection critical cases fail closed 100%; duplicate execution = 0. **Any failure blocks.** |
| **Model quality** | Ollama (or Anthropic if configured) | Locally, `make eval` | Macro-F1 ≥ 0.90, routing ≥ 0.90, evidence coverage, unsupported-claim rate, latency p50/p95. The report is committed to `evals/reports/`. A release checklist item requires a passing report for the release's model, skill and prompt hashes. |

- Datasets: `data/golden/dev/` (prompts may be tuned against it) and `data/golden/heldout/` (never tuned
  against). A dataset manifest has a version and hash.
- Every eval run records: provider, model, model digest, skill hash, prompt hash, tool-schema hash,
  dataset version, seed, git SHA, timestamp and per-case results.
- If the local model misses a quality gate, the report shows "NOT MET". Thresholds are not lowered
  without a documented reason (spec §12).

**Owner decision (2026-11-26):** model quality and safety enforcement stay separate. A model-quality
result can never waive or relax a safety gate, and a safety gate never depends on model accuracy.

## Consequences
Safety does not depend on which model is plugged in, which is the point of the design.
