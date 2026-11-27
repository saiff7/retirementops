"""M4 Stage 7: reports are reproducible and redacted; baselines flag regressions (no database)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from retirementops.evaluations import baseline as B
from retirementops.evaluations import dataset as gd
from retirementops.evaluations import report
from retirementops.evaluations.cli import EvalRun
from retirementops.evaluations.runner import RunMetadata
from tests.unit.test_m4_scorers import _perfect


@pytest.fixture(scope="module")
def golden() -> gd.GoldenDataset:
    return gd.load()


def _meta(golden: gd.GoldenDataset, run_id: str = "20260101T000000Z-safety-all") -> RunMetadata:
    m = golden.manifest
    return RunMetadata(
        run_id=run_id,
        tier="safety",
        split="all",
        repeat_count=1,
        provider="scripted",
        model="scripted-faithful",
        model_digest=None,
        skill="service-ticket-triage@1.1.0",
        skill_sha256="a" * 64,
        prompt_sha256="b" * 64,
        tool_schema_sha256="c" * 64,
        validator_version="draft-validator/2",
        routing_rules_version="routing-rules/1",
        calc_version="contribution-calc/1",
        dataset=m.dataset,
        dataset_version=m.version,
        dataset_aggregate_sha256=m.aggregate_sha256,
        seed=m.seed,
        seed_dataset_hash=m.seed_dataset_hash,
        eval_config={"budgets": {}, "guard": {}},
        git_sha="d" * 40,
        git_dirty=False,
        python="3.12.14",
        started_at="2026-01-01T00:00:00+00:00",
    )


def _run(golden: gd.GoldenDataset, **over: Any) -> EvalRun:
    results = [_perfect(c, script="faithful") for c in golden.cases]
    return EvalRun(_meta(golden, **over), results)


def test_fixed_input_renders_byte_identically(
    golden: gd.GoldenDataset, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(B, "BASELINE_DIR", tmp_path / "no-baselines")
    a = report.write(_run(golden), tmp_path / "a")
    b = report.write(_run(golden), tmp_path / "b")
    for name in ("metadata.json", "results.jsonl", "summary.json", "report.md"):
        assert (a / name).read_bytes() == (b / name).read_bytes(), name


def test_report_files_and_schema(
    golden: gd.GoldenDataset, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(B, "BASELINE_DIR", tmp_path / "no-baselines")
    out = report.write(_run(golden), tmp_path)
    summary = json.loads((out / "summary.json").read_text())
    assert set(summary) >= {
        "metadata",
        "gates",
        "faithful_scores",
        "case_pass",
        "baseline",
        "adversarial",
    }
    RunMetadata.model_validate(json.loads((out / "metadata.json").read_text()))
    assert summary["faithful_scores"]["review_queue_age"] == "not applicable until M5"
    md = (out / "report.md").read_text()
    assert "SYNTHETIC DATA ONLY" in md and "## Safety gates" in md and "## Per-case results" in md
    assert len((out / "results.jsonl").read_text().splitlines()) == 30
    for f in out.iterdir():  # redacted: no participant refs or ticket IDs anywhere
        text = f.read_text()
        assert not report.PART.search(text) and not report.TICKET.search(text), f.name


def test_existing_report_directory_is_never_overwritten(
    golden: gd.GoldenDataset, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(B, "BASELINE_DIR", tmp_path / "no-baselines")
    report.write(_run(golden), tmp_path)
    with pytest.raises(FileExistsError):
        report.write(_run(golden), tmp_path)


def _summary(
    scores: dict[str, Any], case_pass: dict[str, bool], gates: dict[str, bool]
) -> dict[str, Any]:
    return {
        "metadata": {
            "run_id": "r",
            "tier": "quality",
            "split": "dev",
            "model": "m",
            "dataset_version": "1.0.0",
        },
        "scores": scores,
        "case_pass": case_pass,
        "gates": {"results": [{"name": k, "passed": v} for k, v in gates.items()]},
        "quality_targets": [],
    }


def test_baseline_flags_metric_case_gate_and_latency_regressions() -> None:
    base = B.snapshot(
        _summary(
            {
                "routing_accuracy": 0.95,
                "parse_failure_rate": 0.0,
                "latency_run_ms_p95": 1000.0,
                "intent_macro_f1": 0.9,
            },
            {"G-DEV-001": True, "G-DEV-002": True},
            {"schema_validity": True},
        )
    )
    now = _summary(
        {
            "routing_accuracy": 0.90,
            "parse_failure_rate": 0.05,
            "latency_run_ms_p95": 1300.0,
            "intent_macro_f1": 0.95,
        },
        {"G-DEV-001": False, "G-DEV-002": True},
        {"schema_validity": False},
    )
    regs = B.compare(now, base)["regressions"]
    kinds = {(r["kind"], r["item"]) for r in regs}
    assert kinds == {
        ("metric", "routing_accuracy"),
        ("metric", "parse_failure_rate"),
        ("latency", "latency_run_ms_p95"),
        ("case", "G-DEV-001"),
        ("gate", "schema_validity"),
    }  # the improved macro-F1 is not a regression


def test_no_regressions_against_an_identical_baseline() -> None:
    s = _summary({"routing_accuracy": 0.9}, {"G-DEV-001": True}, {"schema_validity": True})
    assert B.compare(s, B.snapshot(s))["regressions"] == []
    assert B.compare(s, None) == {"baseline_run_id": None, "regressions": []}


def test_small_latency_noise_is_not_a_regression() -> None:
    base = B.snapshot(_summary({"latency_run_ms_p95": 1000.0}, {}, {}))
    assert B.compare(_summary({"latency_run_ms_p95": 1200.0}, {}, {}), base)["regressions"] == []


def test_set_baseline_records_a_run(
    golden: gd.GoldenDataset, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(B, "BASELINE_DIR", tmp_path / "baselines")
    out = report.write(_run(golden), tmp_path / "reports")
    path = B.set_baseline(out)
    assert (
        path.parent == tmp_path / "baselines" and path.name == "safety-all-scripted-faithful.json"
    )
    snap = json.loads(path.read_text())
    assert snap["baseline_run_id"] == "20260101T000000Z-safety-all" and len(snap["case_pass"]) == 30
    # The next report is compared with it: a case that now fails is flagged.
    results = [_perfect(c, script="faithful") for c in golden.cases]
    results[0] = _perfect(golden.cases[0], script="faithful", routed_queue="DIGITAL_SUPPORT")
    summary = report.summarise(
        EvalRun(_meta(golden, run_id="20260102T000000Z-safety-all"), results)
    )
    assert any(r["kind"] == "case" for r in summary["baseline"]["regressions"])


def test_baseline_file_names_are_safe() -> None:
    assert B.baseline_path("quality", "dev", "qwen3:14b").name == "quality-dev-qwen3-14b.json"
