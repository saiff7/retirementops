"""Baselines and regression detection (ADR-010 Amendment 1, point 12).

A baseline is a committed file `evals/reports/baselines/<tier>-<split>-<model>.json`, written
only by an explicit, reviewed step (`scripts/run_eval.py --set-baseline <run-dir>`). Every new
report is compared with the matching baseline and flags:
- any metric that moved in the bad direction (any amount for correctness metrics; more than 25%
  for latency);
- any case that passed in the baseline and fails now;
- any gate or quality target that passed in the baseline and fails now.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from retirementops.config import ROOT

BASELINE_DIR = ROOT / "evals" / "reports" / "baselines"
HIGHER_IS_BETTER = (
    "intent_accuracy",
    "intent_macro_f1",
    "routing_accuracy",
    "manual_review_recall",
    "manual_review_precision",
    "evidence_coverage",
    "citation_precision",
    "evidence_ownership_accepted_drafts",
    "accepted_outputs_schema_valid",
    "tool_selection_precision",
    "tool_selection_recall",
    "tool_argument_accuracy",
    "unauthorized_attempts_denied",
    "injection_resistance",
    "case_pass_rate",
)
LOWER_IS_BETTER = (
    "unsupported_claim_rate",
    "unsupported_claim_rate_accepted_drafts",
    "parse_failure_rate",
    "cross_tenant_leaks",
    "duplicate_open_proposals",
    "mock_system_writes",
    "missing_traces",
    "host_feedback_rate",
)
LATENCY = ("latency_run_ms_p95", "latency_model_call_ms_p95")
LATENCY_TOLERANCE = 0.25


def baseline_path(tier: str, split: str, model: str) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")
    return BASELINE_DIR / f"{tier}-{split}-{slug}.json"


def load_baseline(
    tier: str, split: str, model: str, root: Path | None = None
) -> dict[str, Any] | None:
    path = (
        baseline_path(tier, split, model)
        if root is None
        else root / baseline_path(tier, split, model).name
    )
    return json.loads(path.read_text()) if path.exists() else None


def snapshot(summary: dict[str, Any]) -> dict[str, Any]:
    """The parts of a summary a later run is compared against."""
    scores = summary.get("scores") or summary.get("faithful_scores") or {}
    checks = {g["name"]: g["passed"] for g in (summary.get("gates") or {}).get("results", [])}
    checks.update({t["name"]: t["passed"] for t in summary.get("quality_targets") or []})
    return {
        "baseline_run_id": summary["metadata"]["run_id"],
        "tier": summary["metadata"]["tier"],
        "split": summary["metadata"]["split"],
        "model": summary["metadata"]["model"],
        "dataset_version": summary["metadata"]["dataset_version"],
        # Missing in baselines recorded before the scorer was versioned (= eval-scorers/1).
        "scorer_version": summary["metadata"].get("scorer_version", "eval-scorers/1"),
        "scores": {
            k: v
            for k, v in scores.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        },
        "case_pass": summary.get("case_pass", {}),
        "checks": checks,
    }


def compare(summary: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any]:
    if baseline is None:
        return {"baseline_run_id": None, "regressions": []}
    now = snapshot(summary)
    then_scorer = baseline.get("scorer_version", "eval-scorers/1")
    if (
        then_scorer != now["scorer_version"]
        or baseline.get("dataset_version") != now["dataset_version"]
    ):
        # Different scoring rules or cases: a comparison would be meaningless, so none is made.
        # The baseline file is left untouched; re-baselining is a deliberate, reviewed step.
        return {
            "baseline_run_id": baseline["baseline_run_id"],
            "baseline_dataset_version": baseline.get("dataset_version"),
            "comparable": False,
            "reason": (
                f"baseline scored with {then_scorer} on dataset {baseline.get('dataset_version')}; "
                f"this run uses {now['scorer_version']} on dataset {now['dataset_version']}"
            ),
            "regressions": [],
        }
    regressions: list[dict[str, Any]] = []
    for key in HIGHER_IS_BETTER:
        b, c = baseline["scores"].get(key), now["scores"].get(key)
        if b is not None and c is not None and c < b:
            regressions.append({"kind": "metric", "item": key, "baseline": b, "current": c})
    for key in LOWER_IS_BETTER:
        b, c = baseline["scores"].get(key), now["scores"].get(key)
        if b is not None and c is not None and c > b:
            regressions.append({"kind": "metric", "item": key, "baseline": b, "current": c})
    for key in LATENCY:
        b, c = baseline["scores"].get(key), now["scores"].get(key)
        if b and c is not None and c > b * (1 + LATENCY_TOLERANCE):
            regressions.append({"kind": "latency", "item": key, "baseline": b, "current": c})
    for case_id, passed in sorted(baseline.get("case_pass", {}).items()):
        if passed and now["case_pass"].get(case_id) is False:
            regressions.append(
                {"kind": "case", "item": case_id, "baseline": "pass", "current": "fail"}
            )
    for name, passed in sorted(baseline.get("checks", {}).items()):
        if passed and now["checks"].get(name) is False:
            regressions.append(
                {"kind": "gate", "item": name, "baseline": "pass", "current": "fail"}
            )
    return {
        "baseline_run_id": baseline["baseline_run_id"],
        "baseline_dataset_version": baseline.get("dataset_version"),
        "comparable": True,
        "regressions": regressions,
    }


def set_baseline(run_dir: Path) -> Path:
    """Record a finished run as the baseline for its tier, split and model (a reviewed step)."""
    summary = json.loads((run_dir / "summary.json").read_text())
    snap = snapshot(summary)
    path = baseline_path(snap["tier"], snap["split"], snap["model"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap, indent=2, sort_keys=True) + "\n")
    return path
