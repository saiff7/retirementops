"""Alembic environment. Migrations always run as `ro_owner`, never as a runtime role."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine

from retirementops.config import DbRole, get_settings

config = context.config


def _db_name() -> str | None:
    # Programmatic callers (tests) pass attributes; CLI callers may pass `-x db=name`.
    name = config.attributes.get("db_name") or context.get_x_argument(as_dictionary=True).get("db")
    return str(name) if name else None


def run_migrations_online() -> None:
    engine = create_engine(get_settings().dsn(DbRole.OWNER, _db_name()))
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=None, transaction_per_migration=True
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline SQL generation is not supported; run against the dev database.")
run_migrations_online()
