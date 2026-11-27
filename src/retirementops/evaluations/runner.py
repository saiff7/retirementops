"""Run golden cases through the unchanged M3 stack and record per-case results (Stage 3).

For each case: one fresh `Orchestrator` (ACC-14), a real operator principal, the recording wrapper
around the provider, a mock-system snapshot before and after (as `svc_eval_reader`), and a readback
of the run's trace, draft and proposals. The result is a strict `CaseResult` with no ticket, draft
or participant text: only codes, IDs and derived features.

This module never uses privileged identities. The run-and-score process builds its settings with
`Settings.unprivileged()`; provisioning runs earlier, in its own process.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from retirementops.agent_runtime.orchestrator import Orchestrator, RunBudgets, RunOutcome
from retirementops.agent_runtime.providers.contracts import (
    ClassificationResult,
    DraftRequest,
    DraftResponse,
    ModelProvider,
    ToolDecision,
    ToolDecisionRequest,
)
from retirementops.agent_runtime.providers.scripted import Script, ScriptedProvider
from retirementops.agent_runtime.providers.scripts import evidence_calls, faithful_draft
from retirementops.agent_runtime.skills import Skill
from retirementops.config import ROOT, Settings
from retirementops.domain.contributions import CALC_VERSION
from retirementops.domain.draft_validator import VALIDATOR_VERSION
from retirementops.domain.evidence import canonical_json, sha256_hex
from retirementops.domain.routing import ROUTING_RULES_VERSION
from retirementops.evaluations.dataset import GoldenCase, GoldenDataset
from retirementops.evaluations.observe import EVAL_DB, Observer
from retirementops.evaluations.recording import RecordingProvider
from retirementops.mcp_servers.service_ops.schemas import input_schema
from retirementops.mcp_servers.service_ops.tools import TOOLS_BY_NAME
from retirementops.services.container import Container, build_container
from retirementops.services.context import RequestContext
from retirementops.services.errors import ServiceError
from retirementops.services.request_guard import GuardConfig

# Explicit evaluation rate limits (ADR-010 Amendment 1, point 1): a scripted run makes hundreds of
# tool calls in seconds as one principal, which the production per-principal burst (120) would
# refuse. Production and dev defaults are unchanged; this config is recorded with every run.
EVAL_GUARD = GuardConfig(
    client_requests_per_minute=100_000,
    client_burst=100_000,
    auth_failures_per_minute=1_000,
    principal_requests_per_minute=100_000,
    principal_burst=100_000,
    anon_audit_per_client=1_000,
    anon_audit_global=10_000,
)
EVAL_BUDGETS = RunBudgets()  # the production defaults; recorded with every run

AMOUNT = re.compile(r"-?\$\d{1,3}(?:,\d{3})*\.\d{2}")
IDENT = re.compile(r"\b(?:PART-\d{4}|PLAN-\d{3}|PP-\d{4}-\d{2}|ZD-\d{4}|TEN-[A-Z]+|E-\d{3,})\b")
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
# An independent, deliberately small list of claims that an action was taken (not the validator's).
ACTION_CLAIMS = re.compile(
    r"\b(?:we have|we've|has been|have been|was)\s+(?:corrected|refunded|adjusted|updated|"
    r"changed|closed|resolved|approved|sent|paid|reprocessed|fixed)\b",
    re.IGNORECASE,
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolCallRecord(_Strict):
    name: str
    decision: str
    outcome: str


class DraftFeatures(_Strict):
    """What the scorer needs from a draft, without its text."""

    status: str
    validator_version: str | None
    created_by: str
    amounts: list[str]
    identifiers: list[str]
    dates: list[str]
    action_claims: int
    body_sha256: str
    cited: list[dict[str, str]]


class CaseResult(_Strict):
    case_id: str
    split: str
    category: str
    repeat: int
    script: str
    error: str | None = None  # exception class when the run could not start
    final_state: str | None = None
    state_path: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    predicted_intent: str | None = None
    predicted_confidence: float | None = None
    model_risk_flags: list[str] = Field(default_factory=list)
    routed_queue: str | None = None
    finding_status: str | None = None
    draft: DraftFeatures | None = None
    proposals: list[dict[str, str]] = Field(default_factory=list)
    open_proposals_for_ticket: int = 0
    trace_written: bool = False
    trace_found: bool = False
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    evidence: list[dict[str, str]] = Field(default_factory=list)
    evidence_tenants: list[str] = Field(default_factory=list)
    cited_tenants: list[str] = Field(default_factory=list)
    first_pass_calls: list[dict[str, Any]] = Field(default_factory=list)
    all_model_calls: list[dict[str, Any]] = Field(default_factory=list)
    host_feedback_used: bool = False
    parse_failures: int = 0
    accepted_outputs_valid: bool = True  # every accepted model output re-validates strictly
    model_calls: int = 0
    model_errors: list[str] = Field(default_factory=list)
    model_latency_ms: list[float] = Field(default_factory=list)
    run_latency_ms: float = 0.0
    mock_tables_changed: list[str] = Field(default_factory=list)


class RunMetadata(_Strict):
    """ADR-010 Amendment 1, point 10. Every field is required; a run without them aborts."""

    run_id: str
    tier: Literal["safety", "quality"]
    split: str
    repeat_count: int
    provider: str
    model: str
    model_digest: str | None
    skill: str
    skill_sha256: str
    prompt_sha256: str
    tool_schema_sha256: str
    validator_version: str
    routing_rules_version: str
    calc_version: str
    dataset: str
    dataset_version: str
    dataset_aggregate_sha256: str
    seed: int
    seed_dataset_hash: str
    eval_config: dict[str, Any]
    git_sha: str
    git_dirty: bool
    python: str
    started_at: str
    finished_at: str | None = None
    database: Literal["retirementops_eval"] = "retirementops_eval"


class MetadataError(RuntimeError):
    """Run metadata is incomplete; the evaluation aborts rather than report unattributable numbers."""


# ---------------------------------------------------------------- scripted "faithful" model
def faithful_script(case: GoldenCase) -> Script:
    """A well-behaved scripted model for the safety tier: it reports the golden intent with high
    confidence, adds no risk flags (the host must detect them itself), gathers evidence with only
    host-resolved references and drafts only from host facts. It exercises the host, not a model."""
    classification = ClassificationResult(
        intent=case.expected.intent, confidence=0.93, summary="scripted"
    )

    def tools(req: ToolDecisionRequest) -> ToolDecision:
        return evidence_calls(req)

    def draft(req: DraftRequest) -> DraftResponse:
        return faithful_draft(req)

    return Script("faithful", classification, [tools], draft)


ProviderFactory = Callable[[GoldenCase], ModelProvider]


# ---------------------------------------------------------------- metadata
def _git() -> tuple[str, bool]:
    git = shutil.which("git")
    if git is None:
        return "unknown", True
    try:
        sha = subprocess.run(  # noqa: S603 - fixed argv, resolved git binary
            [git, "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = bool(
            subprocess.run(  # noqa: S603 - fixed argv, resolved git binary
                [git, "status", "--porcelain", "--untracked-files=no"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown", True
    return sha, dirty


def prompt_sha256() -> str:
    """The prompt construction code plus every provider module that renders prompts."""
    files = sorted((ROOT / "src/retirementops/agent_runtime/providers").glob("*.py"))
    digest = hashlib.sha256()
    for f in files:
        digest.update(f.name.encode() + b"\0" + f.read_bytes() + b"\0")
    return digest.hexdigest()


def tool_schema_sha256(skill: Skill) -> str:
    schemas = {
        n: input_schema(TOOLS_BY_NAME[n].input_model)  # type: ignore[arg-type]
        for n in sorted({*skill.model_tools, *skill.host_actions})
    }
    return sha256_hex(canonical_json(schemas))


def build_metadata(
    *,
    run_id: str,
    tier: Literal["safety", "quality"],
    split: str,
    repeats: int,
    provider: ModelProvider,
    golden: GoldenDataset,
    skill: Skill,
    extra_config: dict[str, Any] | None = None,
) -> RunMetadata:
    sha, dirty = _git()
    try:
        digest = provider.model_digest()
    except Exception:  # noqa: BLE001 - recorded as missing, and checked below
        digest = None
    if provider.name != "scripted" and not digest:
        raise MetadataError("a live model run must record the model digest")
    config = {
        "budgets": {k: getattr(EVAL_BUDGETS, k) for k in EVAL_BUDGETS.__dataclass_fields__},
        "guard": {k: getattr(EVAL_GUARD, k) for k in EVAL_GUARD.__dataclass_fields__},
        **(extra_config or {}),
    }
    m = golden.manifest
    meta = RunMetadata(
        run_id=run_id,
        tier=tier,
        split=split,
        repeat_count=repeats,
        provider=provider.name,
        model=provider.model,
        model_digest=digest,
        skill=skill.skill_version,
        skill_sha256=skill.sha256,
        prompt_sha256=prompt_sha256(),
        tool_schema_sha256=tool_schema_sha256(skill),
        validator_version=VALIDATOR_VERSION,
        routing_rules_version=ROUTING_RULES_VERSION,
        calc_version=CALC_VERSION,
        dataset=m.dataset,
        dataset_version=m.version,
        dataset_aggregate_sha256=m.aggregate_sha256,
        seed=m.seed,
        seed_dataset_hash=m.seed_dataset_hash,
        eval_config=config,
        git_sha=sha,
        git_dirty=dirty,
        python=platform.python_version(),
        started_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    check_metadata(meta)
    return meta


def check_metadata(meta: RunMetadata) -> None:
    for name, value in meta.model_dump().items():
        if name in {"model_digest", "finished_at"}:
            continue
        if value is None or value == "" or value == {}:
            raise MetadataError(f"run metadata is incomplete: {name}")
    if meta.provider != "scripted" and not meta.model_digest:
        raise MetadataError("run metadata is incomplete: model_digest")


# ---------------------------------------------------------------- one case
def _revalidates(rec: Any) -> bool:
    """Schema-validity gate: re-validate every output the system accepted, strictly."""
    try:
        if rec.classification is not None:
            ClassificationResult.model_validate(rec.classification.model_dump())
        if rec.draft is not None:
            DraftResponse.model_validate(rec.draft.model_dump())
        for turn in rec.tool_turns:
            ToolDecision.model_validate({"tool_calls": turn, "done": True})
    except Exception:  # noqa: BLE001 - any failure means an invalid output was accepted
        return False
    return True


def _features(rb_draft: dict[str, Any], body: str, cited: list[dict[str, Any]]) -> DraftFeatures:
    return DraftFeatures(
        status=str(rb_draft["status"]),
        validator_version=rb_draft.get("validator_version"),
        created_by=str(rb_draft["created_by"]),
        amounts=sorted(set(AMOUNT.findall(body))),
        identifiers=sorted(set(IDENT.findall(body))),
        dates=sorted(set(ISO_DATE.findall(body))),
        action_claims=len(ACTION_CLAIMS.findall(body)),
        body_sha256=sha256_hex(body),
        cited=[
            {k: str(c.get(k, "")) for k in ("system", "record_type", "record_id")} for c in cited
        ],
    )


@dataclass
class CaseRunner:
    container: Container
    observer: Observer
    budgets: RunBudgets = field(default_factory=lambda: EVAL_BUDGETS)

    def operator(self, user_id: str) -> RequestContext:
        token = self.container.identity.issue_dev_token(user_id)
        return RequestContext(self.container.identity.authenticate(token), f"eval-{user_id}")

    def run(
        self,
        case: GoldenCase,
        provider: ModelProvider,
        *,
        repeat: int = 1,
        script_name: str = "",
        orchestrator_factory: Callable[[Container, ModelProvider], Orchestrator] | None = None,
    ) -> CaseResult:
        recorder = RecordingProvider(provider)
        result = CaseResult(
            case_id=case.case_id,
            split=case.split,
            category=case.category,
            repeat=repeat,
            script=script_name or provider.model,
        )
        before = self.observer.snapshot()
        started = time.perf_counter()
        outcome: RunOutcome | None = None
        try:
            orch = (
                orchestrator_factory(self.container, recorder)
                if orchestrator_factory
                else Orchestrator(self.container, recorder, budgets=self.budgets)
            )
            outcome = orch.run(self.operator(case.operator), case.ticket_id)
        except ServiceError as exc:
            result.error = type(exc).__name__
        result.run_latency_ms = round((time.perf_counter() - started) * 1000, 1)
        after = self.observer.snapshot()
        result.mock_tables_changed = sorted(t for t in before if before[t] != after.get(t))

        rec = recorder.recording
        result.parse_failures = rec.parse_failures
        result.model_calls = len(rec.calls)
        result.model_errors = [c.error for c in rec.calls if c.error]
        result.model_latency_ms = [round(c.latency_ms, 1) for c in rec.calls]
        result.first_pass_calls = rec.first_pass_calls
        result.all_model_calls = rec.all_calls
        result.host_feedback_used = any(rec.tool_turn_feedback)
        result.accepted_outputs_valid = _revalidates(rec)
        if rec.classification is not None:
            result.predicted_intent = rec.classification.intent.value
            result.predicted_confidence = rec.classification.confidence
            result.model_risk_flags = sorted(f.value for f in rec.classification.risk_flags)
        if outcome is None:
            return result

        result.final_state = outcome.final_state.value
        result.state_path = [s.value for s in outcome.state_path]
        result.reason_codes = [r.split(":", 1)[0] for r in outcome.reasons]
        result.routed_queue = outcome.routed_queue
        result.finding_status = outcome.finding_status
        result.trace_written = outcome.trace_written

        rb = self.observer.readback(
            case.tenant_id, outcome.run_id, outcome.draft_id, case.ticket_id
        )
        result.trace_found = rb.header is not None
        result.tool_calls = [
            ToolCallRecord(name=t["tool_name"], decision=t["decision"], outcome=t["outcome"])
            for t in rb.tool_calls
        ]
        result.evidence = [
            {"system": e["system"], "record_type": e["record_type"], "record_id": e["record_id"]}
            for e in rb.evidence
        ]
        result.evidence_tenants = sorted(
            {
                t
                for e in rb.evidence
                if (t := self.observer.record_tenant(e["system"], e["record_type"], e["record_id"]))
            }
        )
        if rb.draft is not None and rb.draft_body is not None:
            result.draft = _features(rb.draft, rb.draft_body, rb.cited)
            result.cited_tenants = sorted(
                {
                    t
                    for c in rb.cited
                    if (
                        t := self.observer.record_tenant(
                            str(c.get("system", "")),
                            str(c.get("record_type", "")),
                            str(c.get("record_id", "")),
                        )
                    )
                }
            )
        result.proposals = [
            {
                "queue": str(p["target_queue"]),
                "status": str(p["status"]),
                "by": str(p["created_by"]),
            }
            for p in rb.proposals
        ]
        result.open_proposals_for_ticket = len(
            self.observer.open_proposals(case.tenant_id, case.ticket_id)
        )
        return result


# ---------------------------------------------------------------- the run-and-score process
def build_eval_container(settings: Settings) -> Container:
    if settings.is_privileged:
        raise RuntimeError("the run-and-score process must use Settings.unprivileged()")
    return build_container(settings, db_name=EVAL_DB, guard_config=EVAL_GUARD)


def new_run_id(tier: str, label: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40]
    return f"{stamp}-{tier}-{safe}"


def results_jsonl(results: list[CaseResult]) -> str:
    return "".join(json.dumps(r.model_dump(mode="json"), sort_keys=True) + "\n" for r in results)


def scripted_provider(case: GoldenCase) -> ModelProvider:
    return ScriptedProvider(faithful_script(case))
