# ADR-005: Explicit orchestrator; Ollama, Anthropic and Scripted providers

**Status:** Accepted (2026-11-26). Amended 2026-11-27: [Amendment 1](#amendment-1-2026-11-27-model-choice-moves-to-m4)
was accepted by the owner (M4 Stage 0). The original decision below is unchanged.

## Context
The spec wants a small, explicit orchestrator and a `ModelProvider` protocol with an Ollama default and
an optional Anthropic adapter. A Claude Code subscription is **not** API access.

## Decision
- No agent framework. `agent_runtime/orchestrator.py` is a state machine (see architecture.md §3)
  with explicit budgets: maximum model turns, maximum tool calls, per-call timeout, total wall-clock
  time and maximum payload size.
- `ModelProvider` protocol, per the spec: `classify_ticket`, `choose_tools`, `draft_response`. All of
  them return Pydantic models. Parse failure gets one retry, then manual review.
- Providers:
  - `OllamaProvider` (default): local, using JSON-schema structured output. The model name is
    configurable. The candidate is chosen in M3 by a small dev-set comparison and recorded.
  - `AnthropicProvider` (optional): enabled only if `ANTHROPIC_API_KEY` is set. The model ID is
    configurable.
  - `ScriptedProvider` (tests and CI): returns canned or adversarial outputs, for example a cross-tenant
    tool call, an invented amount, a request to call `close_ticket`, or malformed JSON. This is how CI
    proves the host enforces safety whatever the model does.
- Skills are loaded from `skills/<name>/SKILL.md`. The skill's content hash and version are recorded on
  every run.
- Untrusted content (ticket body, retrieved documents) is wrapped in clearly delimited blocks, and the
  system prompt states that content inside them is data, not instructions. **This is not relied on as a
  control.** The controls are the tool allowlist, the tenant-bound token and output validation.

## Consequences
- The control flow can be read and tested line by line.
- Model quality and safety are tested separately.

## Alternatives
- *LangGraph, Pydantic AI or similar.* Possible later. They add abstraction before the control flow is understood.

## Amendment 1 (2026-11-27): model choice moves to M4

**Status:** Accepted by the owner on 2026-11-27 (M4 Stage 0), committed in `3670c4e`.

### Context
The decision above says the Ollama model "is chosen in M3 by a small dev-set comparison and
recorded". That comparison was not run in M3. M3 used `qwen3:8b`, the configured default, and
checked it only with a smoke run on one ticket (3 of 3 runs reached `PENDING_REVIEW`). A smoke run
is not an evaluation. M4 builds the golden dev set the comparison needs (ADR-010, Amendment 1).

### Decision
- **The comparison moves from M3 to M4** (M4 Stage 6). It replaces "chosen in M3" above.
- **`qwen3:8b` remains the default model** until the owner approves a change. Nothing in M4
  changes the default automatically, whatever the results.
- **Candidates:** `qwen3:8b` and `qwen3:14b`, both installed locally, compared by measurement in
  the quality tier (`scripts/run_eval.py --tier quality`).
- **Splits:**
  - the comparison runs on the **dev** split, with repeats (default 3), and reports the variance;
  - the **held-out** split is reserved for the final evaluation and runs once per model;
  - prompts and skills are never tuned against held-out.
- **Same settings for both models:** the provider settings used in M3 (loopback only,
  temperature 0, fixed seed 42, thinking off, per-call timeout). Every run records the model
  digest and the other ADR-010 metadata.
- **What the comparison reports:**
  - macro-F1 and routing accuracy, against the ADR-010 targets;
  - first-pass tool accuracy and evidence coverage;
  - unsupported-claim rate and parse-failure rate;
  - p50 and p95 latency.
  A missed target shows "NOT MET" and is never lowered.
- **Safety does not depend on the choice:** whichever model is chosen, the safety tier runs with
  `ScriptedProvider` and is unaffected.
- **The owner decides** from the written comparison. The chosen model and its digest are recorded
  here and in `DECISIONS.md`.

### Consequences
- The default is not changed on the strength of a smoke run.
- The comparison costs local time: roughly 10–15 minutes per `qwen3:8b` pass over 30 cases, and
  about twice that for `qwen3:14b` (estimates from M3 smoke runs, to be confirmed by measurement).

### Alternatives considered
- **Keep "chosen in M3" and backfill a result.** Rejected: the comparison was not run, and the
  record must not claim it was.
- **Switch to `qwen3:14b` now.** Rejected: there is no measurement yet.
