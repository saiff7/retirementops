"""`scripts/run_eval.py`: the M4 evaluation entry point (ADR-010 Amendment 1, point 3).

    uv run python scripts/run_eval.py --tier safety
    uv run python scripts/run_eval.py --tier quality --model qwen3:8b --split dev --repeats 3

Two processes, in order (point 1):
1. **Provisioning**, in a child process with the full settings: rebuilds `retirementops_eval` and
   exits. Nothing else runs until it has exited successfully.
2. **Run and score**, in this process, with `Settings.unprivileged()`: it never holds the superuser
   or `ro_owner` secrets and cannot open an owner or superuser connection.

Results go to `evals/reports/<run-id>/`. Reports never contain ticket, draft or participant text.
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from retirementops.config import ROOT, Settings
from retirementops.evaluations import dataset as gd
from retirementops.evaluations import runner as R
from retirementops.evaluations.observe import Observer, reader_engine
from retirementops.governance.logging import configure_logging

REPORTS_DIR = ROOT / "evals" / "reports"


class ProvisioningFailed(RuntimeError):
    pass


def provision_in_child_process() -> None:
    """Run `python -m retirementops.evaluations.provision` and wait for it to exit."""
    proc = subprocess.run(
        [sys.executable, "-m", "retirementops.evaluations.provision"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ProvisioningFailed(f"provisioning exited with {proc.returncode}")


@dataclass
class EvalRun:
    metadata: R.RunMetadata
    results: list[R.CaseResult]
    out_dir: Path | None = None
    extra: dict[str, Any] | None = None


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--tier", choices=["safety", "quality"], required=False)
    ap.add_argument(
        "--set-baseline",
        type=Path,
        default=None,
        metavar="RUN_DIR",
        help="record a finished run as the baseline for its tier/split/model (reviewed step)",
    )
    ap.add_argument("--split", choices=["dev", "heldout", "all"], default=None)
    ap.add_argument("--model", default=None, help="Ollama model for the quality tier")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--out", type=Path, default=REPORTS_DIR)
    ap.add_argument("--label", default=None, help="short label for the run directory")
    ap.add_argument("--no-provision", action="store_true", help="reuse the current eval database")
    ap.add_argument("--no-write", action="store_true", help="do not write a report directory")
    args = ap.parse_args(argv)
    if args.set_baseline is not None:
        return args
    if args.tier is None:
        ap.error("--tier is required")
    if args.split is None:
        args.split = "all" if args.tier == "safety" else "dev"
    if args.tier == "quality" and not args.model:
        ap.error("--model is required for the quality tier")
    if not 1 <= args.repeats <= 10:
        ap.error("--repeats must be between 1 and 10")
    return args


def select_cases(golden: gd.GoldenDataset, split: str) -> tuple[gd.GoldenCase, ...]:
    return golden.cases if split == "all" else golden.split(split)


def run(
    argv: Sequence[str] | None = None,
    *,
    provision: Callable[[], None] | None = None,
    settings_factory: Callable[[], Settings] = Settings.unprivileged,
) -> EvalRun:
    args = parse_args(argv)
    if not args.no_provision:
        # Child process; returns only after it has exited successfully.
        (provision or provision_in_child_process)()
    settings = settings_factory()
    if settings.is_privileged:
        raise RuntimeError("the run-and-score process must not hold privileged secrets")
    configure_logging(settings.ro_log_pepper.get_secret_value().encode(), stream=io.StringIO())
    golden = gd.load()
    cases = select_cases(golden, args.split)
    container = R.build_eval_container(settings)
    engine = reader_engine(settings)
    try:
        observer = Observer(engine, golden.manifest.tenants)
        case_runner = R.CaseRunner(container, observer)
        if args.tier == "safety":
            from retirementops.evaluations import safety

            ev = cast(EvalRun, safety.run_safety_tier(case_runner, golden, cases, args))
        else:
            from retirementops.evaluations import quality

            ev = cast(EvalRun, quality.run_quality_tier(case_runner, golden, cases, args))
    finally:
        container.dispose()
        engine.dispose()
    if not args.no_write:
        from retirementops.evaluations import report

        ev.out_dir = report.write(ev, args.out)
    return ev


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.set_baseline is not None:
        from retirementops.evaluations import baseline

        path = baseline.set_baseline(args.set_baseline)
        print(json.dumps({"baseline": str(path.relative_to(ROOT))}))
        return 0
    try:
        ev = run(argv)
    except ProvisioningFailed as exc:
        print(json.dumps({"error": "provisioning_failed", "message": str(exc)}), file=sys.stderr)
        return 3
    from retirementops.evaluations import report

    summary = report.summarise(ev)
    print(json.dumps(report.console_summary(summary, ev.out_dir), indent=2, sort_keys=True))
    if ev.metadata.tier == "safety" and not summary["gates"]["all_passed"]:
        return 1  # any safety gate failure blocks (ADR-010)
    return 0
