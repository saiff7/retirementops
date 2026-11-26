# ADR-005: Explicit orchestrator; Ollama, Anthropic and Scripted providers

**Status:** Accepted (2026-11-26)

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
