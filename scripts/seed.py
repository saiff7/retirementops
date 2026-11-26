"""Create (or reset) a database, migrate it, and load deterministic synthetic data.

    uv run python scripts/seed.py --seed 42            # dev database from .env
    uv run python scripts/seed.py --seed 42 --reset    # drop and recreate first
    uv run python scripts/seed.py --write-manifest     # refresh data/synthetic/* only

SYNTHETIC DATA ONLY. Local development and CI. Requires the Postgres superuser from .env.
"""

from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import create_engine

from retirementops.config import ROOT, DbRole, get_settings
from retirementops.db.bootstrap import bootstrap
from retirementops.db.migrate import upgrade_head
from retirementops.domain.evidence import canonical_json
from retirementops.seed.generator import DEFAULT_SEED, generate
from retirementops.seed.loader import db_table_hash, load_dataset

OUT = ROOT / "data" / "synthetic"


def write_manifest(seed: int) -> None:
    ds = generate(seed)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "manifest.json").write_text(json.dumps(ds.manifest(), indent=2, sort_keys=True) + "\n")
    (OUT / "ticket_labels.jsonl").write_text(
        "".join(canonical_json(lab) + "\n" for lab in ds.ticket_labels)
    )
    print(
        f"wrote {OUT.relative_to(ROOT)}/manifest.json and ticket_labels.jsonl "
        f"(dataset_hash={ds.content_hash()[:16]}...)"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--db", default=None, help="database name (default: RO_DB_NAME)")
    ap.add_argument("--reset", action="store_true", help="drop and recreate the database first")
    ap.add_argument("--write-manifest", action="store_true", help="only write data/synthetic/*")
    args = ap.parse_args()

    if args.write_manifest:
        write_manifest(args.seed)
        return 0

    settings = get_settings()
    db_name = args.db or settings.ro_db_name
    bootstrap(settings, db_name, drop_existing=args.reset)
    upgrade_head(db_name)
    ds = generate(args.seed)
    owner = create_engine(
        settings.dsn(DbRole.OWNER, db_name),
        connect_args={"options": "-c timezone=UTC"},
        hide_parameters=True,
    )
    try:
        load_dataset(owner, ds)
        loaded = db_table_hash(owner, ds)
    finally:
        owner.dispose()
    ok = loaded == ds.content_hash()
    counts = ", ".join(f"{k.split('.')[1]}={v}" for k, v in ds.counts().items())
    print(f"seed={args.seed} db={db_name} {counts}")
    print(f"dataset_hash={ds.content_hash()} db_matches_generator={ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
