"""The model-quality tier (ADR-010 Amendment 1, point 5; ADR-005 Amendment 1). Local only.

Runs the golden cases with a real local model through the unchanged M3 stack, `--repeats` times,
and reports mean scores, the variance across repeats, the quality targets (met or NOT MET, never
lowered) and the safety gates as observed. The quality tier never blocks and never relaxes a
safety gate. Prompts and skills may be tuned against dev only; held-out is run once per model.
"""

from __future__ import annotations

import argparse
import statistics
from typing import Any

from retirementops.agent_runtime.providers.ollama import OllamaProvider
from retirementops.agent_runtime.skills import load_skill
from retirementops.config import Settings
from retirementops.evaluations import gates as G
from retirementops.evaluations import scorers as S
from retirementops.evaluations.dataset import GoldenCase, GoldenDataset
from retirementops.evaluations.runner import (
    CaseResult,
    CaseRunner,
    build_metadata,
    new_run_id,
)


def ollama(settings: Settings, model: str) -> OllamaProvider:
    return OllamaProvider(
        model=model,
        base_url=settings.ro_ollama_url,
        timeout_seconds=settings.ro_ollama_timeout_seconds,
    )


def run_quality_tier(
    runner: CaseRunner,
    golden: GoldenDataset,
    cases: tuple[GoldenCase, ...],
    args: argparse.Namespace,
) -> Any:
    from retirementops.evaluations.cli import EvalRun

    settings = Settings.unprivileged()
    provider = ollama(settings, args.model)
    meta = build_metadata(
        run_id=new_run_id("quality", args.label or f"{args.model}-{args.split}"),
        tier="quality",
        split=args.split,
        repeats=args.repeats,
        provider=provider,
        golden=golden,
        skill=load_skill("service-ticket-triage"),
        extra_config={
            "ollama": {"temperature": 0, "seed": 42, "think": False},
            "timeout_seconds": settings.ro_ollama_timeout_seconds,
        },
    )
    results: list[CaseResult] = []
    for repeat in range(1, args.repeats + 1):
        for c in cases:
            results.append(
                runner.run(c, ollama(settings, args.model), repeat=repeat, script_name=args.model)
            )
    return EvalRun(meta, results)


def evaluate(
    cases: tuple[GoldenCase, ...] | list[GoldenCase], results: list[CaseResult]
) -> dict[str, Any]:
    repeats = sorted({r.repeat for r in results})
    per_repeat = {n: S.score(cases, [r for r in results if r.repeat == n]) for n in repeats}
    numeric = [
        k
        for k, v in per_repeat[repeats[0]].items()
        if isinstance(v, (int, float)) and not isinstance(v, bool) and k != "cases"
    ]
    mean: dict[str, Any] = {"cases": per_repeat[repeats[0]]["cases"], "repeats": len(repeats)}
    variance: dict[str, dict[str, Any]] = {}
    for k in numeric:
        vals = [per_repeat[n][k] for n in repeats if per_repeat[n][k] is not None]
        if not vals:
            mean[k] = None
            continue
        mean[k] = round(statistics.fmean(vals), 4)
        variance[k] = {
            "min": min(vals),
            "max": max(vals),
            "stdev": round(statistics.pstdev(vals), 4) if len(vals) > 1 else 0.0,
        }
    for k, v in per_repeat[repeats[0]].items():
        if isinstance(v, str):
            mean[k] = v  # "not applicable until M5"
    pairs = S.pair(cases, results)
    observed = G.safety_gates(pairs)
    # In the quality tier the matrix and faithful gates do not apply (no scripted runs).
    observed = [
        g
        for g in observed
        if g.name not in {"adversarial_runs_fail_closed", "faithful_model_reproduces_golden"}
    ]
    case_pass: dict[str, bool] = {}
    for c, r in pairs:
        ok = S.case_passed(c, r)[0]
        case_pass[c.case_id] = case_pass.get(c.case_id, True) and ok
    return {
        "scores": mean,
        "per_repeat": per_repeat,
        "variance": variance,
        "quality_targets": [t.as_dict() for t in G.quality_targets(mean)],
        "gates": G.summarise(observed),
        "case_pass": case_pass,
        "runs": len(results),
    }
