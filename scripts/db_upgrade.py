"""Upgrade a database in place: ensure login roles, ensure the database, run all migrations.

    uv run python scripts/db_upgrade.py            # dev database from .env
    uv run python scripts/db_upgrade.py --db NAME

Never drops data. Run `scripts/dev_bootstrap.py` first so any new role passwords exist in .env.
See docs/operations-migrations.md for rollback.
"""

from __future__ import annotations

import argparse
import sys

from retirementops.config import get_settings
from retirementops.db.migrate import bootstrap_and_upgrade


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None)
    args = ap.parse_args()
    settings = get_settings()
    db_name = args.db or settings.ro_db_name
    bootstrap_and_upgrade(settings, db_name)
    print(f"database {db_name} is at the latest migration")
    return 0


if __name__ == "__main__":
    sys.exit(main())
