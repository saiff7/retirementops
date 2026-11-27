"""M4 evaluation entry point (ADR-010 Amendment 1). SYNTHETIC DATA ONLY.

    uv run python scripts/run_eval.py --tier safety                       # CI gate; exit 1 on any failure
    uv run python scripts/run_eval.py --tier quality --model qwen3:8b --split dev --repeats 3
    uv run python scripts/run_eval.py --set-baseline evals/reports/<run-id>  # reviewed step

Provisioning runs first in its own process (rebuilds `retirementops_eval` only); cases then run and
are scored in this process without the superuser or `ro_owner` secrets. Reports are written to
`evals/reports/<run-id>/` and contain no ticket, draft or participant text.
"""

from __future__ import annotations

import sys

from retirementops.evaluations.cli import main

if __name__ == "__main__":
    sys.exit(main())
