"""Programmatic Alembic upgrade (used by scripts and the test fixture)."""

from __future__ import annotations

from alembic import command
from alembic.config import Config

from retirementops.config import ROOT


def upgrade_head(db_name: str | None = None) -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    if db_name:
        cfg.attributes["db_name"] = db_name
    command.upgrade(cfg, "head")
