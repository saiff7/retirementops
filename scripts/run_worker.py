"""Run the M3 Service Operations worker on one ticket, as a seeded operator. SYNTHETIC DATA ONLY.

    uv run python scripts/run_worker.py ZD-1003 --operator op.alex --provider scripted:good
    uv run python scripts/run_worker.py ZD-1003 --operator op.alex --provider ollama --model qwen3:8b

The operator is a mock identity (not SSO): this script issues a dev token for them, then the
orchestrator mints a run token bound to the ticket. The result is a draft and a routing PROPOSAL
for human review; nothing is sent, assigned or closed. Prints the run outcome as JSON (no ticket
text, no tokens). Requires the database at migration head (`scripts/db_upgrade.py`).
"""

from __future__ import annotations

import argparse
import json
import sys

from retirementops.agent_runtime.orchestrator import Orchestrator
from retirementops.agent_runtime.providers import scripts
from retirementops.agent_runtime.providers.contracts import ModelProvider
from retirementops.agent_runtime.providers.ollama import OllamaProvider
from retirementops.agent_runtime.providers.scripted import ScriptedProvider
from retirementops.config import get_settings
from retirementops.governance.logging import configure_logging
from retirementops.services.container import build_container
from retirementops.services.context import RequestContext
from retirementops.services.errors import ServiceError

SCRIPTS = {"good": scripts.good, **scripts.ADVERSARIAL}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("ticket_id")
    ap.add_argument("--operator", default="op.alex")
    ap.add_argument("--provider", default="scripted:good", help="ollama | scripted:<name>")
    ap.add_argument("--model", default=None, help="Ollama model (default from settings)")
    args = ap.parse_args()

    settings = get_settings()
    configure_logging(settings.ro_log_pepper.get_secret_value().encode(), stream=sys.stderr)
    provider: ModelProvider
    if args.provider == "ollama":
        provider = OllamaProvider(
            model=args.model or settings.ro_ollama_model,
            base_url=settings.ro_ollama_url,
            timeout_seconds=settings.ro_ollama_timeout_seconds,
        )
    elif args.provider.startswith("scripted:") and args.provider[9:] in SCRIPTS:
        provider = ScriptedProvider(SCRIPTS[args.provider[9:]]())
    else:
        ap.error(f"--provider must be ollama or scripted:{{{','.join(SCRIPTS)}}}")

    container = build_container(settings)
    try:
        token = container.identity.issue_dev_token(args.operator)
        operator = RequestContext(container.identity.authenticate(token), "cli-run-worker")
        outcome = Orchestrator(container, provider, kill_switch=settings.ro_agent_kill_switch).run(
            operator, args.ticket_id
        )
    except ServiceError as exc:  # refused before any run started (not an operator, no access)
        print(json.dumps({"error": exc.code, "message": str(exc)}), file=sys.stderr)
        return 2
    finally:
        container.dispose()
    print(json.dumps(outcome.as_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
