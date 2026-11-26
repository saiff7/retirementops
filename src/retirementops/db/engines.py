"""One SQLAlchemy engine per Postgres role (ADR-003). Engines are never shared between roles."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine

from retirementops.config import RUNTIME_ROLES, DbRole, Settings


def make_engine(settings: Settings, role: DbRole, db_name: str | None = None) -> Engine:
    return create_engine(
        settings.dsn(role, db_name),
        pool_size=2,
        max_overflow=2,
        pool_pre_ping=True,
        # Bound parameter values (ticket text, draft bodies, refs) never appear in exception
        # messages, so they cannot reach logs through tracebacks (final review Low-1).
        hide_parameters=True,
        connect_args={
            "application_name": f"retirementops:{role.value}",
            # UTC everywhere; bounded statements so a bad query cannot hang a request.
            "options": "-c timezone=UTC -c statement_timeout=5000 -c lock_timeout=2000",
        },
    )


def make_runtime_engines(settings: Settings, db_name: str | None = None) -> dict[DbRole, Engine]:
    return {role: make_engine(settings, role, db_name) for role in RUNTIME_ROLES}
