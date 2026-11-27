"""Regression tests for the M3 stage 6 review findings (end to end, real services and database).

1. Only an authorised human operator can start a run; refusal happens before any model call.
2. The model-facing evidence token is read-only; host writes use separate, short-lived tokens.
3. (Validator content rules: tests/unit/test_draft_validator.py; end to end below.)
4. (Skill version pins: tests/unit/test_skill_loader.py.)
5. A failed trace write is visible in the audit log.
Plus: one run at a time per Orchestrator, and a clear refusal for malformed tool arguments.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import Engine, text

from retirementops.agent_runtime import orchestrator as orch_module
from retirementops.agent_runtime.orchestrator import Orchestrator
from retirementops.agent_runtime.providers import scripts
from retirementops.agent_runtime.providers.contracts import DraftResponse, ToolCall, ToolDecision
from retirementops.agent_runtime.providers.scripted import Script, ScriptedProvider
from retirementops.domain.authz import AGENT_RUN_SCOPES, RunBinding, Scope
from retirementops.domain.run_state import RunState
from retirementops.domain.taxonomy import Queue
from retirementops.services.container import Container
from retirementops.services.context import RequestContext
from retirementops.services.errors import Forbidden

pytestmark = pytest.mark.integration
TICKET = "ZD-1005"  # seeded ONE_CENT_UNDER (PART-0002, PLAN-001, PP-2026-03), TEN-ALDER
Ctx = Callable[[str], RequestContext]


def _count(engine: Engine, sql: str, **params: Any) -> int:
    with engine.connect() as c:
        return int(c.execute(text(sql), params).scalar_one())


# ---------------------------------------------------------------- 1. operator authorisation
def _agent_ctx(container: Container, ctx_for: Ctx) -> RequestContext:
    binding = RunBinding(
        "RUN-00000000000000AB",
        "op.alex",
        "TEN-ALDER",
        TICKET,
        frozenset({"PART-0002"}),
        frozenset({"PLAN-001"}),
        frozenset({"PP-2026-03"}),
        Queue.PAYROLL_OPERATIONS,
    )
    token = container.identity.issue_agent_run_token(
        ctx_for("op.alex").principal, binding, AGENT_RUN_SCOPES
    )
    return RequestContext(container.identity.authenticate(token), "test-agent")


@pytest.mark.parametrize("caller", ["rev.sam", "comp.jo", "agent"])
def test_non_operators_cannot_start_a_run_or_reach_the_model(
    container: Container, ctx_for: Ctx, owner_engine: Engine, caller: str
) -> None:
    ctx = _agent_ctx(container, ctx_for) if caller == "agent" else ctx_for(caller)
    runs = "SELECT count(*) FROM app.agent_runs"
    refused = "SELECT count(*) FROM app.audit_events WHERE event_type = 'agent_run.refused'"
    before_runs, before_refused = _count(owner_engine, runs), _count(owner_engine, refused)
    provider = ScriptedProvider(scripts.good())
    with pytest.raises(Forbidden):
        Orchestrator(container, provider).run(ctx, TICKET)
    assert provider.requests == []  # no ticket content ever reached the model
    assert _count(owner_engine, runs) == before_runs  # no run, no trace
    assert _count(owner_engine, refused) == before_refused + 1  # but the attempt is audited


# ---------------------------------------------------------------- 2. separate tokens
def test_evidence_token_is_read_only_and_write_tokens_are_short_and_narrow(
    container: Container, ctx_for: Ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    minted: list[tuple[frozenset[Scope], int | None]] = []
    real = container.identity.issue_agent_run_token

    def spy(
        op: Any, binding: Any, scopes: frozenset[Scope], *, ttl_seconds: int | None = None
    ) -> str:
        minted.append((scopes, ttl_seconds))
        return real(op, binding, scopes, ttl_seconds=ttl_seconds)

    monkeypatch.setattr(container.identity, "issue_agent_run_token", spy)
    out = Orchestrator(container, ScriptedProvider(scripts.good())).run(ctx_for("op.alex"), TICKET)
    assert out.final_state is RunState.PENDING_REVIEW, out.reasons
    read = frozenset({Scope.TICKETS_READ, Scope.PLANS_READ, Scope.PAYROLL_READ})
    assert minted == [
        (read, None),  # evidence phase: the only token behind model-chosen calls
        (read | {Scope.DRAFTS_WRITE}, orch_module.WRITE_TOKEN_TTL_SECONDS),  # host: the draft
        (frozenset({Scope.DRAFTS_WRITE, Scope.TICKETS_READ}), orch_module.WRITE_TOKEN_TTL_SECONDS),
    ]
    assert orch_module.WRITE_TOKEN_TTL_SECONDS <= 120


@pytest.mark.parametrize("tool", ["create_reply_draft", "propose_ticket_assignment"])
def test_even_if_the_host_check_failed_the_model_token_cannot_write(
    container: Container,
    ctx_for: Ctx,
    owner_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
) -> None:
    """Defence in depth: disable the host allowlist; the server still refuses the write."""
    monkeypatch.setattr(Orchestrator, "_check_call", lambda self, r, call, resolved: None)
    drafts = "SELECT count(*) FROM app.drafts WHERE created_by = 'agent.worker'"
    before = _count(owner_engine, drafts)
    args = {"ticket_id": TICKET, "target_queue": "PAYROLL_OPERATIONS", "rationale": "x"}
    if tool == "create_reply_draft":
        args = {"ticket_id": TICKET, "body": "x", "evidence_refs": [], "skill_version": "a@1.0.0"}
    turn = ToolDecision(tool_calls=[ToolCall(name=tool, arguments=args)], done=False)
    script = Script("write-attempt", scripts.CONFIDENT, [turn], scripts.faithful_draft)
    out = Orchestrator(container, ScriptedProvider(script)).run(ctx_for("op.alex"), TICKET)
    assert out.final_state is RunState.NEEDS_MANUAL_REVIEW
    assert f"TOOL_ERROR: {tool} forbidden" in out.reasons
    assert _count(owner_engine, drafts) == before


# ---------------------------------------------------------------- 3. validator, end to end
def test_a_draft_with_a_link_waits_for_a_human(container: Container, ctx_for: Ctx) -> None:
    def draft(req: Any) -> DraftResponse:
        body = scripts.faithful_draft(req).body + " Visit https://evil.example/reset to log in."
        return DraftResponse(body=body, needs_human=False)

    script = Script("link", scripts.CONFIDENT, [scripts.evidence_calls], draft)
    out = Orchestrator(container, ScriptedProvider(script)).run(ctx_for("op.alex"), TICKET)
    assert (
        out.final_state is RunState.NEEDS_MANUAL_REVIEW
        and out.draft_status == "NEEDS_MANUAL_REVIEW"
    )
    assert any("LINK_PRESENT" in r for r in out.reasons)


# ---------------------------------------------------------------- 5. trace failure visibility
def _audit(engine: Engine, event: str, run_id: str) -> str | None:
    with engine.connect() as c:
        return c.execute(
            text(
                "SELECT details::text FROM app.audit_events WHERE event_type = :e AND target_ref = :r"
            ),
            {"e": event, "r": run_id},
        ).scalar()


def test_trace_written_is_audited_on_success(
    container: Container, ctx_for: Ctx, owner_engine: Engine
) -> None:
    out = Orchestrator(container, ScriptedProvider(scripts.good())).run(ctx_for("op.alex"), TICKET)
    assert out.trace_written
    assert '"trace_written": true' in (_audit(owner_engine, "agent_run.finished", out.run_id) or "")
    assert _audit(owner_engine, "agent_run.trace_failed", out.run_id) is None


def test_trace_failure_is_never_hidden(
    container: Container, ctx_for: Ctx, owner_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("recorder unavailable")

    monkeypatch.setattr(container.traces, "record", boom)
    out = Orchestrator(container, ScriptedProvider(scripts.good())).run(ctx_for("op.alex"), TICKET)
    assert not out.trace_written
    assert "TRACE_NOT_WRITTEN: RuntimeError" in out.reasons
    finished = _audit(owner_engine, "agent_run.finished", out.run_id) or ""
    failed = _audit(owner_engine, "agent_run.trace_failed", out.run_id) or ""
    assert '"trace_written": false' in finished
    assert out.run_id in failed and "RuntimeError" in failed


# ---------------------------------------------------------------- small safeguards
def test_one_run_at_a_time_per_orchestrator(container: Container, ctx_for: Ctx) -> None:
    errors: list[Exception] = []
    holder: dict[str, Orchestrator] = {}

    def reentrant(req: Any) -> Any:
        try:
            holder["o"].run(ctx_for("op.alex"), TICKET)
        except RuntimeError as exc:
            errors.append(exc)
        return scripts.CONFIDENT

    turns: list[object] = [scripts.evidence_calls, ToolDecision(done=True)] * 2  # for both runs
    script = Script("reentrant", reentrant, turns, scripts.faithful_draft)
    holder["o"] = Orchestrator(container, ScriptedProvider(script))
    out = holder["o"].run(ctx_for("op.alex"), TICKET)
    assert errors and "already running" in str(errors[0])
    assert out.final_state is RunState.PENDING_REVIEW  # the first run is unaffected
    second = holder["o"].run(ctx_for("op.alex"), TICKET)  # free again afterwards
    assert second.final_state is RunState.PENDING_REVIEW


@pytest.mark.parametrize("value", [["PLAN-001"], {"id": "PLAN-001"}])
def test_malformed_arguments_get_a_clear_refusal(
    container: Container, ctx_for: Ctx, value: object
) -> None:
    turn = ToolDecision(
        tool_calls=[ToolCall(name="get_plan_details", arguments={"plan_id": value})], done=False
    )
    script = Script("malformed", scripts.CONFIDENT, [turn], scripts.faithful_draft)
    out = Orchestrator(container, ScriptedProvider(script)).run(ctx_for("op.alex"), TICKET)
    assert out.final_state is RunState.NEEDS_MANUAL_REVIEW
    assert out.reasons[0] == "HOST_REFUSED_TOOL_CALL: host_refused_malformed_arguments"


# ---------------------------------------------------------------- concurrency (second review)
def _in_thread(fn: Callable[[], Any]) -> tuple[Any, list[Any]]:
    import threading

    box: list[Any] = []

    def target() -> None:
        try:
            box.append(fn())
        except Exception as exc:  # noqa: BLE001 - surface to the test
            box.append(exc)

    t = threading.Thread(target=target, daemon=True)
    t.start()
    return t, box


@pytest.mark.parametrize("block_at", ["ticket_read", "model_call", "trace_write"])
def test_two_threads_cannot_run_one_orchestrator_at_once(
    container: Container, ctx_for: Ctx, monkeypatch: pytest.MonkeyPatch, block_at: str
) -> None:
    """Thread A is held at a point in its run; thread B must be refused immediately. Covers the
    start of run() (before the ticket is read) and its tail (after the draft, during the trace)."""
    import threading

    entered, release = threading.Event(), threading.Event()

    def hold() -> None:
        entered.set()
        assert release.wait(10), "thread A was never released"

    classify: Any = scripts.CONFIDENT
    if block_at == "ticket_read":
        real_get = container.tickets.get

        def slow_get(ctx: RequestContext, tid: str) -> Any:
            if threading.current_thread() is not threading.main_thread() and not entered.is_set():
                hold()
            return real_get(ctx, tid)

        monkeypatch.setattr(container.tickets, "get", slow_get)
    elif block_at == "model_call":

        def classify(req: Any) -> Any:
            hold()
            return scripts.CONFIDENT
    else:
        real_record = container.traces.record

        def slow_record(*args: Any, **kwargs: Any) -> None:
            hold()
            real_record(*args, **kwargs)

        monkeypatch.setattr(container.traces, "record", slow_record)

    turns: list[object] = [scripts.evidence_calls, ToolDecision(done=True)]
    o = Orchestrator(
        container, ScriptedProvider(Script("held", classify, turns, scripts.faithful_draft))
    )
    thread_a, a_box = _in_thread(lambda: o.run(ctx_for("op.alex"), TICKET))
    assert entered.wait(10), "thread A never reached the blocking point"
    thread_b, b_box = _in_thread(lambda: o.run(ctx_for("op.alex"), TICKET))
    thread_b.join(10)
    release.set()
    thread_a.join(30)
    assert isinstance(b_box[0], RuntimeError) and "already running" in str(b_box[0])
    assert a_box[0].final_state is RunState.PENDING_REVIEW, a_box[0]  # A is unaffected


def test_the_lock_is_released_after_success_and_after_a_refusal(
    container: Container, ctx_for: Ctx
) -> None:
    turns: list[object] = [scripts.evidence_calls, ToolDecision(done=True)] * 2
    o = Orchestrator(
        container,
        ScriptedProvider(Script("twice", scripts.CONFIDENT, turns, scripts.faithful_draft)),
    )
    with pytest.raises(Forbidden):
        o.run(ctx_for("rev.sam"), TICKET)  # refused: the lock must still be released
    assert o.run(ctx_for("op.alex"), TICKET).final_state is RunState.PENDING_REVIEW
    assert o.run(ctx_for("op.alex"), TICKET).final_state is RunState.PENDING_REVIEW
