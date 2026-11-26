"""DEV ONLY: print a mock-identity token for a seeded fictional user (for MCP Inspector / stdio).

    export RETIREMENTOPS_MCP_TOKEN=$(uv run python scripts/dev_token.py op.alex)

Refuses to run unless RETIREMENTOPS_ENV=dev. Tokens expire after RO_JWT_TTL_SECONDS (default 900).
Not production SSO.
"""

from __future__ import annotations

import sys

from retirementops.config import get_settings
from retirementops.services.container import build_container
from retirementops.services.errors import NotFound


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: dev_token.py <user_id>", file=sys.stderr)
        return 2
    settings = get_settings()
    container = build_container(settings)
    try:
        print(container.identity.issue_dev_token(sys.argv[1]))
        return 0
    except NotFound:
        print("not available: dev mode off, unknown user, or no signing key", file=sys.stderr)
        return 1
    finally:
        container.dispose()


if __name__ == "__main__":
    sys.exit(main())
