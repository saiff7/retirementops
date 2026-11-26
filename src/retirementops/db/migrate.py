"""Programmatic Alembic upgrade (used by scripts and the test fixture)."""

from __future__ import annotations

from alembic import command
from alembic.config import Config

from retirementops.config import ROOT, Settings
from retirementops.db.bootstrap import create_database, ensure_roles


def upgrade_head(db_name: str | None = None) -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    if db_name:
        cfg.attributes["db_name"] = db_name
    command.upgrade(cfg, "head")


def downgrade_to(revision: str, db_name: str | None = None) -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    if db_name:
        cfg.attributes["db_name"] = db_name
    command.downgrade(cfg, revision)


def bootstrap_and_upgrade(settings: Settings, db_name: str) -> None:
    """Safe upgrade path (review Low-3): roles first, because migrations GRANT to login roles that
    `ro_owner` cannot create, then the database (kept if it exists), then all migrations.
    Idempotent. Never drops data."""
    ensure_roles(settings)
    create_database(settings, db_name, drop_existing=False)
    upgrade_head(db_name)
