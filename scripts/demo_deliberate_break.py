"""Show that the M4 safety tier fails when a safety invariant is broken. SYNTHETIC DATA ONLY.

    uv run python scripts/demo_deliberate_break.py

What it does, without touching your working tree or any branch:
1. creates a temporary, detached git worktree of HEAD;
2. links (does not copy) your git-ignored `.env` and `keys/` into it;
3. applies `evals/deliberate-break/disable-unsupported-amount-check.patch`, which stops the draft
   validator from flagging invented amounts;
4. runs `scripts/run_eval.py --tier safety` there (it rebuilds only `retirementops_eval`);
5. removes the worktree.

The demonstration succeeds (exit 0) only if the broken build's safety run exits 1 with the
`unsupported_financial_values_accepted` gate failed. This is the local equivalent of pushing a
deliberately broken branch and watching CI go red; nothing here is pushed or committed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATCH = ROOT / "evals" / "deliberate-break" / "disable-unsupported-amount-check.patch"
GATE = "unsupported_financial_values_accepted"


def _run(cmd: list[str], cwd: Path, **kw: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=False, **kw)  # type: ignore[call-overload]  # noqa: S603


def main() -> int:
    git = shutil.which("git")
    uv = shutil.which("uv")
    if git is None or uv is None:
        print("git and uv are required", file=sys.stderr)
        return 2
    tmp = Path(tempfile.mkdtemp(prefix="ro-break-"))
    wt = tmp / "wt"
    try:
        added = _run([git, "worktree", "add", "--detach", str(wt), "HEAD"], ROOT)
        if added.returncode != 0:
            print("could not create a temporary worktree", file=sys.stderr)
            return 2
        for name in (".env", "keys"):
            os.symlink(ROOT / name, wt / name)
        if _run([git, "apply", str(PATCH)], wt).returncode != 0:
            print("the deliberate-break patch no longer applies", file=sys.stderr)
            return 2
        env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
        broken = _run(
            [
                uv,
                "run",
                "python",
                "scripts/run_eval.py",
                "--tier",
                "safety",
                "--out",
                str(tmp / "reports"),
            ],
            wt,
            env=env,
        )
        try:
            summary = json.loads(broken.stdout)
        except json.JSONDecodeError:
            summary = {}
        result = {
            "patch": str(PATCH.relative_to(ROOT)),
            "broken_build_exit_code": broken.returncode,
            "failed_gates": summary.get("failed_gates"),
            "demonstrated": broken.returncode == 1 and GATE in (summary.get("failed_gates") or []),
        }
        print(json.dumps(result, indent=2))
        return 0 if result["demonstrated"] else 1
    finally:
        _run([git, "worktree", "remove", "--force", str(wt)], ROOT)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
