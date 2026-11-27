"""The explicit M3 orchestrator (ADR-005; spec §8 runtime loop; architecture.md §3 state machine).

One run handles one ticket for one human operator. In order:

1. RECEIVED: the kill switch is checked; the ticket is read with the OPERATOR's authority.
2. The model classifies the (masked, delimited) ticket. -> CLASSIFIED
3. The host resolves tenant, participant, plan and pay period from trusted data and adds
   deterministic risk flags; the routing rules engine decides queue and manual review.
   M3 drafts only contribution discrepancies; anything else ends in manual review. -> RESOLVED
4. A READ-ONLY run token is minted for the operator, bound to that ticket, those subjects and the
   routed queue. The model proposes read-only tool calls; the host checks each (allowlist,
   well-formed arguments within the resolution, size, count), runs each batch in the skill's order
   and executes it through the real MCP server. Any refusal or tool error fails the run closed.
5. The host computes the contribution finding deterministically, checks every record it used was
   retrieved by the tools, and re-applies routing with the finding. -> EVIDENCE_GATHERED
6. The model drafts from preformatted host facts. The HOST stores it through `create_reply_draft`
   with a separate, short-lived write token that the model never sees; the server-side validator
   decides PENDING_REVIEW or NEEDS_MANUAL_REVIEW. -> DRAFTED -> ...
7. The host proposes the routed queue (a proposal only, again with its own short write token),
   writes the trace through the recorder role and audits the outcome, including whether the trace
   was written. Nothing is sent, assigned, closed or changed.

Only a human operator assigned to the ticket's tenant may start a run; that is checked before any
ticket content reaches a model. One Orchestrator instance runs one ticket at a time.

Any exception, timeout, budget overrun or malformed output moves the run to NEEDS_MANUAL_REVIEW.
The model never receives write tools, money values from tool results, or unmasked identifiers.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from functools import partial
from typing import Any

from retirementops.agent_runtime.mcp_client import McpToolClient, ToolResult
from retirementops.agent_runtime.providers.contracts import (
    ClassificationRequest,
    ClassificationResult,
    DraftRequest,
    DraftResponse,
    ModelProvider,
    ProviderError,
    ProviderOutputError,
    ResolvedContext,
    SkillPrompt,
    ToolCall,
    ToolDecision,
    ToolDecisionRequest,
    ToolResultView,
    ToolView,
)
from retirementops.agent_runtime.providers.prompts import ticket_view
from retirementops.agent_runtime.skills import Skill, load_skill
from retirementops.clock import Clock, utc_now
from retirementops.domain.authz import RunBinding, Scope
from retirementops.domain.contributions import ContributionFinding
from retirementops.domain.evidence import canonical_json, sha256_hex
from retirementops.domain.money import Money, format_money, format_percent
from retirementops.domain.records import ContributionType, Ticket
from retirementops.domain.routing import RoutingDecision, decide_route
from retirementops.domain.run_state import RunState, RunStateMachine
from retirementops.domain.taxonomy import TERMINAL_TICKET_STATUSES, Intent
from retirementops.governance.logging import get_logger
from retirementops.mcp_servers.service_ops.schemas import input_schema
from retirementops.mcp_servers.service_ops.tools import TOOLS_BY_NAME
from retirementops.services.container import Container
from retirementops.services.context import RequestContext
from retirementops.services.errors import Forbidden
from retirementops.services.run_traces import EvidenceTrace, RunHeader, ToolCallTrace
from retirementops.services.ticket_resolution import Resolution

log = get_logger("retirementops.agent_runtime")
DEFAULT_SKILL = "service-ticket-triage"
WRITE_TOKEN_TTL_SECONDS = 120  # host-only write tokens live just long enough for one action
_SCALAR = (str, int, float, bool, type(None))


@dataclass(frozen=True, slots=True)
class RunBudgets:
    max_model_calls: int = 8  # includes the single retry after malformed output
    max_tool_turns: int = 4
    max_tool_calls: int = 12  # model-requested evidence calls
    max_wall_seconds: float = 600.0
    max_argument_bytes: int = 2048


class _FailClosed(Exception):
    """Internal: stop the run and record a value-free reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class RunOutcome:
    run_id: str
    ticket_id: str
    final_state: RunState
    state_path: tuple[RunState, ...]
    reasons: tuple[str, ...]
    intent: str | None = None
    routed_queue: str | None = None
    finding_status: str | None = None
    draft_id: str | None = None
    draft_status: str | None = None
    proposal_id: str | None = None
    tool_calls: int = 0
    trace_written: bool = False

    def as_dict(self) -> dict[str, Any]:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        d["final_state"] = self.final_state.value
        d["state_path"] = [s.value for s in self.state_path]
        d["reasons"] = list(self.reasons)
        return d


@dataclass
class _Run:
    run_id: str
    correlation_id: str
    started_at: datetime
    started_mono: float
    sm: RunStateMachine = field(default_factory=RunStateMachine)
    reasons: list[str] = field(default_factory=list)
    calls: list[ToolCallTrace] = field(default_factory=list)
    evidence: dict[tuple[str, str, str], EvidenceTrace] = field(default_factory=dict)
    model_calls: int = 0
    evidence_calls: int = 0
    ticket: Ticket | None = None
    classification: ClassificationResult | None = None
    decision: RoutingDecision | None = None
    finding: ContributionFinding | None = None
    resolution: Resolution | None = None
    draft_id: str | None = None
    draft_status: str | None = None
    proposal_id: str | None = None


class Orchestrator:
    def __init__(
        self,
        container: Container,
        provider: ModelProvider,
        *,
        skill: Skill | None = None,
        budgets: RunBudgets | None = None,
        kill_switch: bool = False,
        clock: Clock = utc_now,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._c, self._provider = container, provider
        self._skill = skill or load_skill(DEFAULT_SKILL)
        self._budgets = budgets or RunBudgets()
        self._kill_switch, self._clock, self._mono = kill_switch, clock, monotonic
        self._skill_prompt = SkillPrompt(
            self._skill.name, self._skill.version, self._skill.instructions
        )
        # One run at a time per instance: a non-blocking lock taken at the very start of run()
        # and released only after the run, proposal, trace and audit have all finished.
        self._lock = threading.Lock()
        self._active: _Run | None = None  # the run the wall-clock budget is measured against
        # The model-facing token is read-only: the skill's scopes minus drafts:write.
        self._evidence_scopes = self._skill.run_scopes - {Scope.DRAFTS_WRITE}
        for tool in self._skill.model_tools:
            if not TOOLS_BY_NAME[tool].scopes <= self._evidence_scopes:
                raise ValueError(f"model tool {tool} needs more than read scopes")

    # ------------------------------------------------------------ public entry point
    def run(self, operator: RequestContext, ticket_id: str) -> RunOutcome:
        """Run the worker on one ticket. Raises NotFound/Forbidden only if the operator cannot see
        the ticket (no run starts); every other problem ends the run in NEEDS_MANUAL_REVIEW."""
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("this Orchestrator is already running a ticket; use one per run")
        try:
            return self._run_locked(operator, ticket_id)
        finally:
            self._active = None
            self._lock.release()

    def _run_locked(self, operator: RequestContext, ticket_id: str) -> RunOutcome:
        self._require_operator(operator, None, ticket_id)  # before the ticket is even read
        ticket = self._c.tickets.get(operator, ticket_id)  # operator authority; 404 otherwise
        self._require_operator(operator, ticket.tenant_id, ticket_id)  # before any model call
        r = _Run(
            run_id=f"RUN-{secrets.token_hex(8).upper()}",
            correlation_id=uuid.uuid4().hex,
            started_at=self._clock(),
            started_mono=self._mono(),
            ticket=ticket,
        )
        self._active = r
        try:
            self._execute(operator, r, ticket)
        except _FailClosed as exc:
            r.reasons.append(exc.reason)
            r.sm.fail()
        except ProviderError as exc:
            r.reasons.append(f"MODEL_{type(exc).__name__.upper()}")
            r.sm.fail()
        except Exception as exc:  # noqa: BLE001 - fail closed, never crash the caller
            log.error("agent_run_failed", run_id=r.run_id, error_type=type(exc).__name__)
            r.reasons.append(f"INTERNAL_ERROR: {type(exc).__name__}")
            r.sm.fail()
        if r.sm.state is not RunState.PENDING_REVIEW:
            r.sm.fail()
        self._propose(operator, r, ticket)
        written = self._write_trace(operator, r, ticket)
        self._audit(operator, r, ticket, written)
        return self._outcome(r, ticket, written)

    def _require_operator(
        self, operator: RequestContext, tenant_id: str | None, ticket_id: str
    ) -> None:
        try:
            self._c.identity.require_run_operator(operator.principal, tenant_id)
        except Forbidden:
            self._c.audit.record(
                operator,
                "agent_run.refused",
                target_ref=ticket_id,
                tenant_id=tenant_id,
                details={"policy_reason": "caller is not an operator for this tenant"},
            )
            raise

    # ------------------------------------------------------------ the pipeline
    def _execute(self, operator: RequestContext, r: _Run, ticket: Ticket) -> None:
        if self._kill_switch:
            raise _FailClosed("KILL_SWITCH_ON")
        if ticket.status in TERMINAL_TICKET_STATUSES:
            raise _FailClosed("TICKET_IS_SOLVED_OR_CLOSED")
        view = ticket_view(ticket.ticket_id, ticket.subject, ticket.body)

        r.classification = self._model(
            r,
            lambda: self._provider.classify_ticket(ClassificationRequest(self._skill_prompt, view)),
        )
        r.sm.advance(RunState.CLASSIFIED)

        resolution = r.resolution = self._c.resolution.resolve(ticket)
        flags = set(resolution.flags) | set(r.classification.risk_flags)  # model can only add
        r.decision = decide_route(
            intent=r.classification.intent,
            confidence=r.classification.confidence,
            flags=flags,
        )
        if r.decision.requires_manual_review:
            raise _FailClosed("ROUTING: " + "; ".join(r.decision.reasons))
        if r.classification.intent is not Intent.CONTRIBUTION_DISCREPANCY:
            raise _FailClosed(f"NO_AUTOMATED_DRAFT_FOR_INTENT: {r.classification.intent.value}")
        if not resolution.unique:
            raise _FailClosed("UNRESOLVED: " + "; ".join(resolution.problems or ("ambiguous",)))
        r.sm.advance(RunState.RESOLVED)

        binding = self._binding(operator, r, resolution)
        token = self._c.identity.issue_agent_run_token(
            operator.principal, binding, self._evidence_scopes
        )
        tools = McpToolClient(self._c, token)  # read-only: the model's calls can never write
        run_ctx = RequestContext(self._c.identity.authenticate(token), r.correlation_id)
        ref, plan = resolution.participant_refs[0], resolution.plan_ids[0]
        period = resolution.pay_periods[0]

        def gaps() -> list[dict[str, Any]]:
            """Records the deterministic calculation needs that the tools have not retrieved."""
            f = self._c.contributions.get_finding(run_ctx, plan, ref, period.pay_period_id)
            rule_types = {c.rule_id: c.component.value for c in f.components}
            missing: list[dict[str, str]] = []
            for e in f.evidence_refs:
                have = r.evidence.get((e.system, e.record_type, e.record_id))
                if have is None or have.content_hash != e.content_hash:
                    gap: dict[str, Any] = {"record_type": e.record_type, "record_id": e.record_id}
                    call = _call_for(e.record_type, rule_types.get(e.record_id), ref, plan, period)
                    if call:  # built from the host's resolution, so it stays inside the binding
                        gap["call"] = call
                    missing.append(gap)
            return missing

        self._gather(r, view, resolution, tools, gaps)
        r.finding = self._c.contributions.get_finding(run_ctx, plan, ref, period.pay_period_id)
        r.decision = decide_route(
            intent=r.classification.intent,
            confidence=r.classification.confidence,
            flags=flags,
            finding_status=r.finding.status,
        )
        if r.decision.requires_manual_review:
            raise _FailClosed(f"FINDING_BLOCKS_DRAFT: {r.finding.status.value}")
        missing = [
            e
            for e in r.finding.evidence_refs
            if (e.system, e.record_type, e.record_id) not in r.evidence
            or r.evidence[(e.system, e.record_type, e.record_id)].content_hash != e.content_hash
        ]
        if missing:
            raise _FailClosed(f"EVIDENCE_NOT_RETRIEVED: {len(missing)} record(s)")
        r.sm.advance(RunState.EVIDENCE_GATHERED)

        facts = _facts(r.finding)
        draft: DraftResponse = self._model(
            r,
            lambda: self._provider.draft_response(
                DraftRequest(self._skill_prompt, view, facts, r.decision.queue.value)  # type: ignore[union-attr]
            ),
        )
        if draft.needs_human:
            raise _FailClosed("MODEL_ASKED_FOR_A_HUMAN")
        refs = [_ref(ticket.source)] + [_ref(e) for e in r.finding.evidence_refs]
        result = self._host_action(
            r,
            self._writer(operator, r, resolution, "create_reply_draft"),
            "create_reply_draft",
            {
                "ticket_id": ticket.ticket_id,
                "body": draft.body,
                "evidence_refs": refs,
                "skill_version": self._skill.skill_version,
                "idempotency_key": r.run_id,
            },
        )
        r.draft_id, r.draft_status = result.data["draft_id"], result.data["status"]
        r.sm.advance(RunState.DRAFTED)
        if r.draft_status == "PENDING_REVIEW":
            r.sm.advance(RunState.PENDING_REVIEW)
        else:
            issues = result.data.get("validator", {}).get("issues", [])
            raise _FailClosed("VALIDATOR: " + ", ".join(issues or ["failed"]))

    def _binding(self, operator: RequestContext, r: _Run, res: Resolution | None) -> RunBinding:
        return RunBinding(
            run_id=r.run_id,
            operator_id=operator.principal.principal_id,
            tenant_id=r.ticket.tenant_id,  # type: ignore[union-attr]
            ticket_id=r.ticket.ticket_id,  # type: ignore[union-attr]
            participant_refs=frozenset(res.participant_refs if res else ()),
            plan_ids=frozenset(res.plan_ids if res else ()),
            pay_period_ids=frozenset(p.pay_period_id for p in res.pay_periods)
            if res
            else frozenset(),
            permitted_queue=r.decision.queue if r.decision else None,
        )

    def _writer(
        self, operator: RequestContext, r: _Run, res: Resolution | None, action: str
    ) -> McpToolClient:
        """A separate, short-lived token for ONE host action, never shown to the model. Its scopes
        are the action's own, plus the read scopes needed to cite evidence for a draft."""
        if action not in self._skill.host_actions:
            raise _FailClosed(f"HOST_ACTION_NOT_IN_SKILL: {action}")
        scopes = TOOLS_BY_NAME[action].scopes
        if action == "create_reply_draft":
            scopes = scopes | self._evidence_scopes
        token = self._c.identity.issue_agent_run_token(
            operator.principal,
            self._binding(operator, r, res),
            scopes & self._skill.run_scopes,
            ttl_seconds=WRITE_TOKEN_TTL_SECONDS,
        )
        return McpToolClient(self._c, token)

    # ------------------------------------------------------------ evidence loop
    def _gather(
        self,
        r: _Run,
        view: Any,
        res: Resolution,
        tools: McpToolClient,
        gaps: Callable[[], list[dict[str, Any]]],
    ) -> None:
        offered = tuple(
            ToolView(
                n,
                TOOLS_BY_NAME[n].description,
                input_schema(TOOLS_BY_NAME[n].input_model),  # type: ignore[arg-type]
            )
            for n in self._skill.model_tools
        )
        resolved = ResolvedContext(
            res.participant_refs,
            res.plan_ids,
            tuple(p.pay_period_id for p in res.pay_periods),
            tuple(p.period_end.isoformat() for p in res.pay_periods),
        )
        results: list[ToolResultView] = []
        done_calls: set[str] = set()
        seen: set[str] = set()
        for _turn in range(self._budgets.max_tool_turns):
            remaining = self._budgets.max_tool_calls - r.evidence_calls
            request = ToolDecisionRequest(
                self._skill_prompt, view, resolved, offered, tuple(results), remaining
            )
            decision: ToolDecision = self._model(r, partial(self._provider.choose_tools, request))
            # Sequence (host-enforced): evidence reads happen only here, between RESOLVED and
            # EVIDENCE_GATHERED; each batch runs in the skill's documented order (stable; unknown
            # names sort last and are refused by the checks); write tools are host-only, later.
            rank = {n: i for i, n in enumerate(self._skill.model_tools)}
            ordered = sorted(decision.tool_calls, key=lambda c: rank.get(c.name, len(rank)))
            for call in ordered:
                key = canonical_json({"n": call.name, "a": call.arguments})
                if key in seen:  # identical repeat: not executed again; say so
                    results.append(
                        ToolResultView(
                            call.name, False, '{"ok": false, "error": "already retrieved"}'
                        )
                    )
                    continue
                self._check_call(r, call, resolved)  # security checks: fail closed
                seen.add(key)
                result = self._execute_call(r, tools, call.name, call.arguments)
                done_calls.add(call.name)
                results.append(ToolResultView(call.name, True, _minimise(result)))
            if decision.done or not decision.tool_calls:
                missing = gaps()
                if not missing:
                    return
                # The host's own completeness check, fed back so the model can finish the job.
                # Still bounded by the turn, call and model budgets.
                feedback = {"ok": False, "missing_evidence": missing}
                results.append(ToolResultView("host_evidence_check", False, json.dumps(feedback)))
        # Out of turns: the completeness check below fails the run if anything is still missing.

    def _check_call(self, r: _Run, call: ToolCall, resolved: ResolvedContext) -> None:
        args_json = canonical_json(call.arguments)

        def refuse(outcome: str) -> None:
            self._trace(r, call.name, "deny", outcome, args_json, None, 0.0)
            raise _FailClosed(f"HOST_REFUSED_TOOL_CALL: {outcome}")

        if call.name not in self._skill.model_tools:
            refuse("host_refused_unlisted_tool")
        if not all(isinstance(v, _SCALAR) for v in call.arguments.values()):
            refuse("host_refused_malformed_arguments")  # tools take scalar arguments only
        if len(args_json.encode()) > self._budgets.max_argument_bytes:
            refuse("host_refused_oversized_arguments")
        if r.evidence_calls >= self._budgets.max_tool_calls:
            refuse("host_refused_call_budget")
        allowed = {
            "participant_ref": set(resolved.participant_refs),
            "plan_id": set(resolved.plan_ids),
            "pay_period": set(resolved.pay_period_ids),
            "effective_date": set(resolved.pay_period_ends),
        }
        for key, values in allowed.items():
            if key in call.arguments and call.arguments[key] not in values:
                refuse("host_refused_reference_outside_resolution")

    def _execute_call(
        self, r: _Run, tools: McpToolClient, name: str, args: dict[str, Any]
    ) -> ToolResult:
        self._check_time()
        r.evidence_calls += 1
        result = tools.call(name, args)
        self._record(r, result, args)
        if not result.ok:
            raise _FailClosed(f"TOOL_ERROR: {name} {result.error}")
        return result

    def _host_action(
        self, r: _Run, tools: McpToolClient, name: str, args: dict[str, Any]
    ) -> ToolResult:
        if name not in self._skill.host_actions:
            raise _FailClosed(f"HOST_ACTION_NOT_IN_SKILL: {name}")
        self._check_time()
        result = tools.call(name, args)
        self._record(r, result, args)
        if not result.ok:
            raise _FailClosed(f"TOOL_ERROR: {name} {result.error}")
        return result

    # ------------------------------------------------------------ model calls and budgets
    def _model[T](self, r: _Run, call: Callable[[], T]) -> T:
        """One model call, with one retry after malformed output (ADR-005)."""
        for attempt in (1, 2):
            self._check_time()
            if r.model_calls >= self._budgets.max_model_calls:
                raise _FailClosed("BUDGET_MODEL_CALLS")
            r.model_calls += 1
            try:
                return call()
            except ProviderOutputError:
                if attempt == 2:
                    raise
        raise _FailClosed("BUDGET_MODEL_CALLS")  # pragma: no cover - loop always returns/raises

    def _check_time(self) -> None:
        r = self._active
        if r is not None and self._mono() - r.started_mono > self._budgets.max_wall_seconds:
            raise _FailClosed("BUDGET_WALL_CLOCK")

    # ------------------------------------------------------------ proposal, trace, audit
    def _propose(self, operator: RequestContext, r: _Run, ticket: Ticket) -> None:
        if r.decision is None or ticket.status in TERMINAL_TICKET_STATUSES:
            return
        try:
            res = r.resolution if RunState.RESOLVED in r.sm.path else None
            writer = self._writer(operator, r, res, "propose_ticket_assignment")
            rationale = (
                f"Routing rules {r.decision.rules_version}: intent {r.decision.intent.value}"
                + (
                    f"; manual review: {'; '.join(r.decision.reasons)}"
                    if r.decision.reasons
                    else ""
                )
            )[:1000]
            result = writer.call(
                "propose_ticket_assignment",
                {
                    "ticket_id": ticket.ticket_id,
                    "target_queue": r.decision.queue.value,
                    "rationale": rationale,
                },
            )
            self._record(r, result, {"ticket_id": ticket.ticket_id, "queue": r.decision.queue})
            if result.ok:
                r.proposal_id = result.data.get("proposal_id")
            else:
                r.reasons.append(f"PROPOSAL_NOT_CREATED: {result.error}")
        except Exception as exc:  # noqa: BLE001 - a proposal failure never hides the outcome
            r.reasons.append(f"PROPOSAL_NOT_CREATED: {type(exc).__name__}")

    def _record(self, r: _Run, result: ToolResult, args: dict[str, Any]) -> None:
        decision = "allow" if result.ok else ("deny" if result.error else "error")
        self._trace(
            r,
            result.name,
            decision,
            "ok" if result.ok else (result.error or "error"),
            canonical_json(args),
            result.result_sha256,
            result.latency_ms,
        )
        if result.ok:
            for src in _sources(result.data):
                key = (src["system"], src["record_type"], src["record_id"])
                if key not in r.evidence and len(r.evidence) < 100:
                    r.evidence[key] = EvidenceTrace(
                        len(r.evidence) + 1,
                        src["system"],
                        src["record_type"],
                        src["record_id"],
                        str(src["record_version"]),
                        src["content_hash"],
                        datetime.fromisoformat(src["retrieved_at"]),
                    )

    def _trace(
        self,
        r: _Run,
        name: str,
        decision: str,
        outcome: str,
        args_json: str,
        result_sha: str | None,
        latency_ms: float,
    ) -> None:
        if len(r.calls) >= 200:
            return
        safe_name = name if name.replace("_", "").isalpha() and name.islower() else "<invalid>"
        safe_outcome = "".join(ch for ch in outcome.lower() if ch.isalpha() or ch == "_")[:40]
        r.calls.append(
            ToolCallTrace(
                seq=len(r.calls) + 1,
                tool_name=safe_name[:64],
                decision=decision,
                outcome=safe_outcome or "error",
                args_sha256=sha256_hex(args_json),
                args_bytes=len(args_json.encode()),
                result_sha256=result_sha,
                latency_ms=Decimal(str(latency_ms)),
                called_at=self._clock(),
            )
        )

    def _write_trace(self, operator: RequestContext, r: _Run, ticket: Ticket) -> bool:
        c = r.classification
        header = RunHeader(
            run_id=r.run_id,
            tenant_id=ticket.tenant_id,
            ticket_id=ticket.ticket_id,
            started_by=operator.principal.principal_id,
            correlation_id=r.correlation_id,
            skill_name=self._skill.name,
            skill_version=self._skill.skill_version,
            skill_sha256=self._skill.sha256,
            provider=self._provider.name,
            model_name=self._provider.model[:128],
            model_digest=_safe_digest(self._provider),
            final_state=r.sm.state.value,
            state_path=tuple(s.value for s in r.sm.path),
            state_reasons=tuple(x[:300] for x in r.reasons),
            intent=c.intent.value if c else None,
            confidence=Decimal(str(round(c.confidence, 3))) if c else None,
            routed_queue=r.decision.queue.value if r.decision else None,
            routing_rules_version=r.decision.rules_version if r.decision else None,
            requires_manual_review=r.sm.state is not RunState.PENDING_REVIEW,
            finding_status=r.finding.status.value if r.finding else None,
            draft_id=r.draft_id,
            proposal_id=r.proposal_id,
            model_turns=r.model_calls,
            started_at=r.started_at,
            finished_at=self._clock(),
        )
        try:
            self._c.traces.record(header, r.calls, list(r.evidence.values()))
            return True
        except Exception as exc:  # noqa: BLE001 - never hidden: logged AND audited below
            log.error("agent_trace_write_failed", run_id=r.run_id, error_type=type(exc).__name__)
            r.reasons.append(f"TRACE_NOT_WRITTEN: {type(exc).__name__}")
            self._c.audit.record(
                operator,
                "agent_run.trace_failed",
                target_ref=r.run_id,
                tenant_id=ticket.tenant_id,
                details={
                    "run_id": r.run_id,
                    "ticket_id": ticket.ticket_id,
                    "final_state": r.sm.state.value,
                    "draft_id": r.draft_id,
                    "error_type": type(exc).__name__,
                },
            )
            return False

    def _audit(self, operator: RequestContext, r: _Run, ticket: Ticket, written: bool) -> None:
        self._c.audit.record(
            operator,
            "agent_run.finished",
            target_ref=r.run_id,
            tenant_id=ticket.tenant_id,
            details={
                "run_id": r.run_id,
                "ticket_id": ticket.ticket_id,
                "final_state": r.sm.state.value,
                "intent": r.classification.intent.value if r.classification else None,
                "routed_queue": r.decision.queue.value if r.decision else None,
                "draft_id": r.draft_id,
                "proposal_id": r.proposal_id,
                "skill_version": self._skill.skill_version,
                "provider": self._provider.name,
                "reason_codes": sorted({x.split(":", 1)[0] for x in r.reasons}),
                "trace_written": written,
            },
        )

    def _outcome(self, r: _Run, ticket: Ticket, written: bool) -> RunOutcome:
        return RunOutcome(
            run_id=r.run_id,
            ticket_id=ticket.ticket_id,
            final_state=r.sm.state,
            state_path=r.sm.path,
            reasons=tuple(r.reasons),
            intent=r.classification.intent.value if r.classification else None,
            routed_queue=r.decision.queue.value if r.decision else None,
            finding_status=r.finding.status.value if r.finding else None,
            draft_id=r.draft_id,
            draft_status=r.draft_status,
            proposal_id=r.proposal_id,
            tool_calls=len(r.calls),
            trace_written=written,
        )


# ---------------------------------------------------------------- helpers
def _ref(source: Any) -> dict[str, str]:
    return {
        "system": source.system,
        "record_type": source.record_type,
        "record_id": source.record_id,
        "content_hash": source.content_hash,
    }


def _sources(data: Any) -> list[dict[str, str]]:
    """Every `source` block in a tool result (records retrieved through MCP)."""
    found: list[dict[str, str]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            src = node.get("source")
            if isinstance(src, dict) and {
                "system",
                "record_type",
                "record_id",
                "content_hash",
            } <= set(src):
                found.append({k: str(v) for k, v in src.items()})
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return found


def _minimise(result: ToolResult) -> str:
    """What the model sees of a tool result: which records were retrieved, never their values
    (data-classification: amounts reach the model only as formatted values from the finding)."""
    summary = {
        "ok": result.ok,
        "retrieved": [
            {"record_type": s["record_type"], "record_id": s["record_id"]}
            for s in _sources(result.data)
        ],
    }
    if "status" in result.data and isinstance(result.data["status"], str):
        summary["status"] = result.data["status"]  # e.g. rule selection SELECTED/MISSING/CONFLICT
    return json.dumps(summary, sort_keys=True)[:4000]


def _facts(f: ContributionFinding) -> dict[str, str]:
    facts = {
        "participant_ref": f.participant_ref,
        "plan_id": f.plan_id,
        "pay_period_id": f.pay_period_id,
        "period_end": f.determination_date,
        "finding_status": f.status.value,
    }
    if f.eligible_pay is not None:
        facts["eligible_pay"] = format_money(f.eligible_pay)
    if f.effective_deferral_pct is not None:
        facts["deferral_pct"] = format_percent(f.effective_deferral_pct)
    names = {
        ContributionType.EMPLOYEE_DEFERRAL: "employee",
        ContributionType.EMPLOYER_MATCH: "employer",
    }
    for comp in f.components:
        n = names[comp.component]
        facts[f"{n}_reported"] = format_money(comp.reported)
        facts[f"{n}_expected"] = format_money(comp.expected)
        facts[f"{n}_difference"] = format_money(Money(abs(comp.variance.amount)))
    return facts


def _call_for(
    record_type: str, contribution_type: str | None, ref: str, plan: str, period: Any
) -> dict[str, Any] | None:
    """The read call that returns a missing record, using only host-resolved references."""
    if record_type == "plan":
        return {"name": "get_plan_details", "arguments": {"plan_id": plan}}
    if record_type == "enrollment":
        return {
            "name": "get_participant_enrollment",
            "arguments": {"participant_ref": ref, "plan_id": plan},
        }
    if record_type == "plan_rule" and contribution_type:
        return {
            "name": "get_applicable_plan_rule",
            "arguments": {
                "plan_id": plan,
                "effective_date": period.period_end.isoformat(),
                "contribution_type": contribution_type,
            },
        }
    if record_type in {"payroll_row", "pay_period"}:
        return {
            "name": "get_payroll_contributions",
            "arguments": {
                "participant_ref": ref,
                "plan_id": plan,
                "pay_period": period.pay_period_id,
            },
        }
    return None


def _safe_digest(provider: ModelProvider) -> str | None:
    try:
        d = provider.model_digest()
    except Exception:  # noqa: BLE001 - the digest is metadata; never fail the trace on it
        return None
    return d[:128] if d else None
