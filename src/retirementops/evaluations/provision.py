"""Build the dedicated evaluation database `retirementops_eval` (ADR-010 Amendment 1, points 1, 1a).

    uv run python -m retirementops.evaluations.provision        # rebuild it (destroys only it)

Runs as its own process, with the full settings, and exits before any case runs. It is the only
evaluation code that uses the privileged identities, and only for setup:

* **Cluster step**, as the Postgres superuser (through the existing `db.bootstrap`): refresh the
  cluster-wide login roles, drop and recreate `retirementops_eval` owned by `ro_owner`, revoke every
  `PUBLIC` database privilege, grant CONNECT to the runtime roles and to `svc_eval_reader`, and set
  the reader's `default_transaction_read_only` in this database.
* **Database step**, as `ro_owner`: migrate to head, load seed 42, check the content hash against
  the golden manifest, then grant `svc_eval_reader` its fixed, reviewed SELECT-only privileges.

Every step re-checks the database name. Any other name, including the dev database
`retirementops` and the test database `retirementops_test`, is refused before anything connects.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass

from psycopg import sql
from sqlalchemy import Engine, create_engine, text

from retirementops.config import DbRole, Settings, get_settings
from retirementops.db.bootstrap import create_database, ensure_roles
from retirementops.db.migrate import upgrade_head
from retirementops.evaluations import dataset as golden
from retirementops.seed.generator import generate
from retirementops.seed.loader import db_table_hash, load_dataset

EVAL_DB = "retirementops_eval"
READER = DbRole.EVAL_READER.value

# The scoring reader's complete allowlist (ADR-010 Amendment 1, point 1a). Nothing else.
READER_SCHEMAS = ("zendesk_mock", "planadmin_mock", "payroll_mock", "servicekb_mock", "app", "sec")
READER_TABLES = (
    "zendesk_mock.organizations",
    "zendesk_mock.tickets",
    "zendesk_mock.ticket_comments",
    "zendesk_mock.ticket_events",
    "planadmin_mock.tenants",
    "planadmin_mock.plans",
    "planadmin_mock.plan_rules",
    "planadmin_mock.participants",
    "planadmin_mock.enrollments",
    "payroll_mock.pay_periods",
    "payroll_mock.payroll_files",
    "payroll_mock.payroll_rows",
    "servicekb_mock.policy_snippets",
    "app.drafts",
    "app.draft_versions",
    "app.action_proposals",
    "app.agent_runs",
    "app.tool_calls",
    "app.evidence_items",
)
READER_FUNCTIONS = ("sec.tenant_scope()",)


class ProvisionError(RuntimeError):
    """Provisioning refused or failed. Nothing outside `retirementops_eval` is touched."""


@dataclass(frozen=True, slots=True)
class ProvisionResult:
    db_name: str
    seed: int
    seed_dataset_hash: str
    seconds: float

    def as_dict(self) -> dict[str, object]:
        return {
            "db_name": self.db_name,
            "seed": self.seed,
            "seed_dataset_hash": self.seed_dataset_hash,
            "seconds": round(self.seconds, 2),
        }


def require_eval_db(name: str) -> str:
    """The name guard. Exactly `retirementops_eval`, nothing else, compared as a whole string."""
    if not isinstance(name, str) or name != EVAL_DB:
        raise ProvisionError(f"refusing to provision a database other than {EVAL_DB}")
    return name


def _owner_engine(settings: Settings, db_name: str) -> Engine:
    require_eval_db(db_name)
    return create_engine(
        settings.dsn(DbRole.OWNER, db_name),
        hide_parameters=True,
        connect_args={"options": "-c timezone=UTC"},
    )


def cluster_step(settings: Settings, db_name: str = EVAL_DB) -> None:
    """Superuser: roles, fresh database, PUBLIC revoked, CONNECT grants, reader read-only default."""
    import psycopg

    require_eval_db(db_name)
    ensure_roles(settings)  # cluster-wide refresh: same attributes, same .env passwords
    require_eval_db(db_name)  # re-checked immediately before the drop
    create_database(settings, db_name, drop_existing=True)
    with psycopg.connect(**settings.superuser_conninfo(), autocommit=True) as conn:  # type: ignore[arg-type]
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(require_eval_db(db_name)), sql.Identifier(READER)
            )
        )
        conn.execute(
            sql.SQL("ALTER ROLE {} IN DATABASE {} SET default_transaction_read_only = on").format(
                sql.Identifier(READER), sql.Identifier(require_eval_db(db_name))
            )
        )


def grant_reader(owner: Engine) -> None:
    """ro_owner: the reader's fixed SELECT-only allowlist (schemas, tables, one function)."""
    with owner.begin() as conn:
        db = conn.execute(text("SELECT current_database()")).scalar_one()
        require_eval_db(db)
        for schema in READER_SCHEMAS:
            conn.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO {READER}'))
        for qualified in READER_TABLES:
            schema, name = qualified.split(".")
            conn.execute(text(f'GRANT SELECT ON TABLE "{schema}"."{name}" TO {READER}'))
        for fn in READER_FUNCTIONS:
            conn.execute(text(f"GRANT EXECUTE ON FUNCTION {fn} TO {READER}"))


def verify_seed_hash(owner: Engine, expected: str) -> str:
    """Re-read every seeded table and compare with the golden manifest's seed hash."""
    with owner.connect() as conn:
        require_eval_db(conn.execute(text("SELECT current_database()")).scalar_one())
    actual = db_table_hash(owner, generate(42))
    if actual != expected:
        raise ProvisionError("the evaluation database does not hold the expected seed data")
    return actual


def database_step(settings: Settings, db_name: str = EVAL_DB) -> str:
    """ro_owner: migrate, seed, verify, grant the reader."""
    require_eval_db(db_name)
    manifest = golden.load().manifest
    upgrade_head(require_eval_db(db_name))
    owner = _owner_engine(settings, db_name)
    try:
        ds = generate(manifest.seed)
        load_dataset(owner, ds)
        digest = verify_seed_hash(owner, manifest.seed_dataset_hash)
        grant_reader(owner)
    finally:
        owner.dispose()
    return digest


def provision(settings: Settings, db_name: str = EVAL_DB) -> ProvisionResult:
    require_eval_db(db_name)  # before anything connects
    started = time.monotonic()
    cluster_step(settings, db_name)
    digest = database_step(settings, db_name)
    return ProvisionResult(db_name, 42, digest, time.monotonic() - started)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--db", default=EVAL_DB, help=f"must be {EVAL_DB}")
    args = ap.parse_args(argv)
    try:
        result = provision(get_settings(), args.db)
    except ProvisionError as exc:
        print(json.dumps({"error": "provision_refused", "message": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result.as_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
