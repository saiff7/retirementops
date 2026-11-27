"""Evaluation reports (ADR-010 Amendment 1, points 10-12).

`evals/reports/<run-id>/` holds:
- `metadata.json`: the complete run metadata;
- `results.jsonl`: one redacted CaseResult per run;
- `summary.json`: scores, gates, quality targets and the baseline comparison;
- `report.md`: the same for people.

**Privacy:** reports contain no ticket or participant text. Draft bodies never leave memory (only
features and a hash are recorded). Participant and ticket references are replaced by placeholders
relative to the case (`{subject.participant}`, `{other.participant}`, `{subject.ticket}`,
`{other.ticket}`), and employer employee refs are removed. Cases are identified by golden case ID.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from retirementops.evaluations import baseline as B
from retirementops.evaluations.dataset import GoldenCase, load
from retirementops.evaluations.runner import CaseResult

PART = re.compile(r"\bPART-\d{4}\b")
TICKET = re.compile(r"\bZD-\d{4}\b")
EMPLOYEE_REF = re.compile(r"\bE-\d{3,}\b")


def _redact_str(value: str, case: GoldenCase) -> str:
    def part(m: re.Match[str]) -> str:
        return (
            "{subject.participant}"
            if m.group(0) == case.subject.participant_ref
            else "{other.participant}"
        )

    def ticket(m: re.Match[str]) -> str:
        return "{subject.ticket}" if m.group(0) == case.ticket_id else "{other.ticket}"

    value = PART.sub(part, value)
    value = TICKET.sub(ticket, value)
    return EMPLOYEE_REF.sub("{employee_ref}", value)


def redact(obj: Any, case: GoldenCase) -> Any:
    if isinstance(obj, str):
        return _redact_str(obj, case)
    if isinstance(obj, list):
        return [redact(v, case) for v in obj]
    if isinstance(obj, dict):
        return {k: redact(v, case) for k, v in obj.items()}
    return obj


def redacted_results(results: list[CaseResult]) -> list[dict[str, Any]]:
    cases = load().by_id()
    return [redact(r.model_dump(mode="json"), cases[r.case_id]) for r in results]


def summarise(ev: Any) -> dict[str, Any]:
    """Scores, gates and quality targets for a finished run (safety or quality tier)."""
    cases = load().cases
    if ev.metadata.tier == "safety":
        from retirementops.evaluations import safety

        body = safety.evaluate(cases, ev.results)
    else:
        from retirementops.evaluations import quality

        body = quality.evaluate(cases, ev.results)
    summary = {"metadata": ev.metadata.model_dump(mode="json"), **body}
    summary["baseline"] = B.compare(
        summary, B.load_baseline(ev.metadata.tier, ev.metadata.split, ev.metadata.model)
    )
    return summary


def write(ev: Any, out_root: Path) -> Path:
    out: Path = out_root / ev.metadata.run_id
    out.mkdir(parents=True, exist_ok=False)
    summary = summarise(ev)
    (out / "metadata.json").write_text(
        json.dumps(summary["metadata"], indent=2, sort_keys=True) + "\n"
    )
    (out / "results.jsonl").write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in redacted_results(ev.results))
    )
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n"
    )
    (out / "report.md").write_text(markdown(summary, per_case_rows(ev)))
    assert_report_is_clean(out)
    return out


def per_case_rows(ev: Any) -> list[dict[str, Any]]:
    from retirementops.evaluations import scorers as S

    cases = load().cases
    return S.per_case(cases, ev.results)


class ReportLeak(RuntimeError):
    """A report file contains a participant reference, ticket ID or employee reference."""


def assert_report_is_clean(out: Path) -> None:
    """Refuse to leave a report that contains participant refs, ticket IDs or employee refs.
    Ticket and draft text never reach a CaseResult in the first place."""
    for f in sorted(out.iterdir()):
        text = f.read_text()
        for pattern in (PART, TICKET, EMPLOYEE_REF):
            if pattern.search(text):
                f.unlink()
                raise ReportLeak(f"{f.name} contained a restricted reference and was removed")


def _fmt(v: Any) -> str:
    if v is None:
        return "–"
    if isinstance(v, float):
        return f"{v:.4f}".rstrip("0").rstrip(".") if v != int(v) else f"{v:.1f}"
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True)
    return str(v)


def markdown(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    m = summary["metadata"]
    lines = [
        f"# Evaluation report: {m['run_id']}",
        "",
        "SYNTHETIC DATA ONLY. No ticket, draft or participant text appears in this report.",
        "",
        "## Run metadata",
        "",
        "| Field | Value |",
        "|---|---|",
    ]
    for key in (
        "tier",
        "split",
        "repeat_count",
        "provider",
        "model",
        "model_digest",
        "skill",
        "skill_sha256",
        "prompt_sha256",
        "tool_schema_sha256",
        "validator_version",
        "routing_rules_version",
        "calc_version",
        "scorer_version",
        "dataset",
        "dataset_version",
        "dataset_aggregate_sha256",
        "seed",
        "seed_dataset_hash",
        "git_sha",
        "git_dirty",
        "python",
        "started_at",
        "database",
    ):
        lines.append(f"| {key} | `{_fmt(m.get(key))}` |")
    gates = summary.get("gates")
    if gates:
        lines += [
            "",
            "## Safety gates (any failure blocks)",
            "",
            "| Gate | Requirement | Value | Result |",
            "|---|---|---|---|",
        ]
        for g in gates["results"]:
            lines.append(
                f"| {g['name']} | {g['requirement']} | `{_fmt(g['value'])}` | {'PASS' if g['passed'] else '**FAIL**'} |"
            )
        lines.append("")
        lines.append(f"**All safety gates passed: {gates['all_passed']}**")
    targets = summary.get("quality_targets")
    if targets:
        lines += [
            "",
            "## Quality targets (reported, never blocking, never lowered)",
            "",
            "| Target | Requirement | Value | Result |",
            "|---|---|---|---|",
        ]
        for t in targets:
            lines.append(
                f"| {t['name']} | {t['requirement']} | `{_fmt(t['value'])}` | {'MET' if t['passed'] else '**NOT MET**'} |"
            )
    scores = summary.get("scores") or summary.get("faithful_scores")
    if scores:
        title = (
            "Metrics (mean over repeats)"
            if "scores" in summary
            else "Metrics (faithful scripted runs)"
        )
        lines += ["", f"## {title}", "", "| Metric | Value |", "|---|---|"]
        for k, v in scores.items():
            lines.append(f"| {k} | `{_fmt(v)}` |")
    variance = summary.get("variance")
    if variance:
        lines += [
            "",
            "## Variance across repeats",
            "",
            "| Metric | Min | Max | Std dev |",
            "|---|---|---|---|",
        ]
        for k, v in variance.items():
            lines.append(
                f"| {k} | `{_fmt(v['min'])}` | `{_fmt(v['max'])}` | `{_fmt(v['stdev'])}` |"
            )
    adv = summary.get("adversarial")
    if adv:
        lines += [
            "",
            "## Adversarial matrix and probes",
            "",
            "| Script | Runs | Failed closed | Reached PENDING_REVIEW |",
            "|---|---|---|---|",
        ]
        for name, row in sorted(adv.items()):
            lines.append(
                f"| {name} | {row['runs']} | {row['failed_closed']} | {row['pending_review']} |"
            )
    base = summary.get("baseline") or {}
    lines += ["", "## Baseline comparison", ""]
    if not base.get("baseline_run_id"):
        lines.append("No baseline recorded for this tier, split and model yet.")
    elif base.get("comparable") is False:
        lines.append(
            f"Baseline `{base['baseline_run_id']}` is **not comparable**: {base['reason']}. "
            "No regressions are computed; re-baselining is a deliberate, reviewed step."
        )
    else:
        lines.append(
            f"Baseline: `{base['baseline_run_id']}`. Regressions: **{len(base['regressions'])}**."
        )
        if base["regressions"]:
            lines += ["", "| Kind | Item | Baseline | This run |", "|---|---|---|---|"]
            for reg in base["regressions"]:
                lines.append(
                    f"| {reg['kind']} | {reg['item']} | `{_fmt(reg['baseline'])}` | `{_fmt(reg['current'])}` |"
                )
    lines += [
        "",
        "## Per-case results",
        "",
        "| Case | Split | Category | Script | Repeat | Pass | Failures | Evidence | Unsupported claims | Parse failures | Host feedback | Run ms |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['case_id']} | {r['split']} | {r['category']} | {r['script']} | {r['repeat']} | "
            f"{'yes' if r['passed'] else '**no**'} | {', '.join(r['failures']) or '–'} | {r['evidence'] or '–'} | "
            f"{', '.join(r['unsupported_claims']) or '–'} | {r['parse_failures']} | {'yes' if r['host_feedback_used'] else 'no'} | {r['run_latency_ms']} |"
        )
    lines.append("")
    return "\n".join(lines)


def console_summary(summary: dict[str, Any], out_dir: Path | None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "run_id": summary["metadata"]["run_id"],
        "tier": summary["metadata"]["tier"],
        "report": str(out_dir) if out_dir else None,
    }
    if summary.get("gates"):
        out["safety_gates_passed"] = summary["gates"]["all_passed"]
        out["failed_gates"] = summary["gates"]["failed"]
    if summary.get("quality_targets"):
        out["quality_targets"] = {
            t["name"]: ("MET" if t["passed"] else "NOT MET") for t in summary["quality_targets"]
        }
    if summary.get("baseline"):
        out["regressions"] = len(summary["baseline"].get("regressions", []))
        if summary["baseline"].get("comparable") is False:
            out["baseline"] = "not comparable (scorer or dataset version changed)"
    return out
