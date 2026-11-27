"""M4 Stage 3: the recording wrapper, the read-only observer and the case runner."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from retirementops.agent_runtime.providers import scripts
from retirementops.agent_runtime.providers.scripted import Script, ScriptedProvider
from retirementops.config import Settings
from retirementops.evaluations import dataset as gd
from retirementops.evaluations import runner as R
from retirementops.evaluations.observe import Observer, reader_engine

pytestmark = pytest.mark.integration
VOLATILE = {"run_latency_ms", "model_latency_ms"}


def _case(golden: gd.GoldenDataset, case_id: str) -> gd.GoldenCase:
    return golden.by_id()[case_id]


def _stable(r: R.CaseResult) -> dict[str, Any]:
    d = r.model_dump(mode="json")
    for k in VOLATILE:
        d.pop(k)
    for p in d["proposals"]:
        p.pop("id", None)
    return d


@pytest.mark.parametrize("case_id", ["G-DEV-002", "G-DEV-011", "G-DEV-019"])
def test_scripted_run_produces_a_valid_result(
    case_runner: R.CaseRunner, golden: gd.GoldenDataset, case_id: str
) -> None:
    case = _case(golden, case_id)
    r = case_runner.run(case, R.scripted_provider(case), script_name="faithful")
    R.CaseResult.model_validate(r.model_dump())  # strict schema round trip
    assert r.error is None and r.trace_written and r.trace_found
    assert r.final_state == case.expected.final_state
    assert r.routed_queue == case.expected.queue.value
    assert r.mock_tables_changed == []
    assert r.predicted_intent == case.expected.intent.value
    if case.expected.final_state == "PENDING_REVIEW":
        assert r.draft is not None and r.draft.status == "PENDING_REVIEW"
        assert r.draft.created_by == "agent.worker"
        assert r.first_pass_calls and not r.host_feedback_used
    else:
        assert r.draft is None


def test_results_contain_no_ticket_or_draft_text(
    case_runner: R.CaseRunner, golden: gd.GoldenDataset, dataset: Any
) -> None:
    case = _case(golden, "G-DEV-002")
    r = case_runner.run(case, R.scripted_provider(case))
    blob = R.results_jsonl([r])
    ticket = next(t for t in dataset.tables["zendesk_mock.tickets"] if t["ticket_id"] == "ZD-1005")
    assert ticket["body"][:30] not in blob and ticket["subject"] not in blob
    assert "Thank you for raising this" not in blob  # the draft body is reduced to features


def test_repeated_scripted_runs_are_identical_apart_from_timing(
    case_runner: R.CaseRunner, golden: gd.GoldenDataset
) -> None:
    case = _case(golden, "G-DEV-003")
    a = case_runner.run(case, R.scripted_provider(case))
    b = case_runner.run(case, R.scripted_provider(case))
    assert _stable(a) == _stable(b)


def test_recording_counts_parse_failures_the_orchestrator_retries(
    case_runner: R.CaseRunner, golden: gd.GoldenDataset
) -> None:
    case = _case(golden, "G-DEV-002")
    answers = iter(['{"intent": "CONTRIBUTION_DISCREPANCY", "confidence": ', scripts.CONFIDENT])

    def classify(_req: Any) -> Any:
        return next(answers)

    script = Script("flaky", classify, [scripts.evidence_calls], scripts.faithful_draft)
    r = case_runner.run(case, ScriptedProvider(script))
    assert r.parse_failures == 1 and "ProviderOutputError" in r.model_errors
    assert r.final_state == "PENDING_REVIEW"  # the orchestrator's single retry succeeded


def test_first_pass_choices_are_kept_apart_from_host_assisted_ones(
    case_runner: R.CaseRunner, golden: gd.GoldenDataset
) -> None:
    import json

    from retirementops.agent_runtime.providers.contracts import ToolCall, ToolDecision

    case = _case(golden, "G-DEV-002")

    def partial(req: Any) -> ToolDecision:
        calls = [
            c
            for c in scripts.evidence_calls(req).tool_calls
            if c.arguments.get("contribution_type") != "EMPLOYEE_DEFERRAL"
        ]
        return ToolDecision(tool_calls=calls, done=True)

    def follow(req: Any) -> ToolDecision:
        missing = json.loads(req.results[-1].content)["missing_evidence"]
        return ToolDecision(tool_calls=[ToolCall(**missing[0]["call"])], done=True)

    script = Script("feedback", scripts.CONFIDENT, [partial, follow], scripts.faithful_draft)
    r = case_runner.run(case, ScriptedProvider(script))
    assert r.final_state == "PENDING_REVIEW" and r.host_feedback_used
    assert len(r.first_pass_calls) == 4 and len(r.all_model_calls) == 5


def test_a_missing_trace_is_recorded_not_skipped(
    case_runner: R.CaseRunner, golden: gd.GoldenDataset, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _case(golden, "G-DEV-001")

    def broken(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("trace store unavailable")

    monkeypatch.setattr(case_runner.container.traces, "record", broken)
    r = case_runner.run(case, R.scripted_provider(case))
    assert not r.trace_written and not r.trace_found
    assert "TRACE_NOT_WRITTEN" in r.reason_codes


def test_metadata_is_complete_and_incomplete_metadata_aborts(golden: gd.GoldenDataset) -> None:
    from retirementops.agent_runtime.skills import load_skill

    case = golden.cases[0]
    meta = R.build_metadata(
        run_id="t",
        tier="safety",
        split="all",
        repeats=1,
        provider=R.scripted_provider(case),
        golden=golden,
        skill=load_skill("service-ticket-triage"),
    )
    assert meta.dataset_version == golden.manifest.version
    assert meta.seed_dataset_hash == golden.manifest.seed_dataset_hash
    assert len(meta.prompt_sha256) == len(meta.tool_schema_sha256) == 64
    assert meta.eval_config["guard"]["principal_burst"] == R.EVAL_GUARD.principal_burst
    with pytest.raises(R.MetadataError):
        R.check_metadata(meta.model_copy(update={"skill_sha256": ""}))
    with pytest.raises(R.MetadataError):
        R.check_metadata(meta.model_copy(update={"provider": "ollama", "model_digest": None}))

    class NoDigest:
        name, model = "ollama", "qwen3:8b"

        def model_digest(self) -> None:
            return None

    with pytest.raises(R.MetadataError):
        R.build_metadata(
            run_id="t",
            tier="quality",
            split="dev",
            repeats=1,
            provider=NoDigest(),  # type: ignore[arg-type]
            golden=golden,
            skill=load_skill("service-ticket-triage"),
        )


def test_run_and_score_container_refuses_privileged_settings(eval_db: str) -> None:
    with pytest.raises(RuntimeError, match="unprivileged"):
        R.build_eval_container(Settings())


def test_observer_is_read_only_and_bound_to_the_eval_database(
    observer: Observer, unprivileged: Settings, test_db: str
) -> None:
    with observer._tx(("TEN-ALDER",)) as c, pytest.raises(DBAPIError):
        c.execute(text("DELETE FROM zendesk_mock.tickets"))
    with pytest.raises(ValueError):
        reader_engine(unprivileged, test_db)
    snap = observer.snapshot()
    assert snap["zendesk_mock.tickets"].startswith("121:")  # all tenants are visible
    assert snap["planadmin_mock.tenants"].startswith("3:")
