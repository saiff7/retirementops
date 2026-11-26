"""Cluster-level bootstrap. Run as the Postgres superuser, **local development and CI only**.

Creates one login role per connector identity (ADR-003) and a database owned by `ro_owner`.
Schemas, tables, grants and RLS policies are created by Alembic migrations, run as `ro_owner`.
"""

from __future__ import annotations

import psycopg
from psycopg import sql

from retirementops.config import RUNTIME_ROLES, DbRole, Settings

# Attributes for every role this project creates. None can bypass RLS or escalate.
_ROLE_ATTRS = "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"


def ensure_roles(settings: Settings) -> None:
    with psycopg.connect(**settings.superuser_conninfo(), autocommit=True) as conn:  # type: ignore[arg-type]
        for role in DbRole:
            password = settings.role_password(role).get_secret_value()
            exists = conn.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s", (role.value,)
            ).fetchone()
            verb = sql.SQL("ALTER ROLE") if exists else sql.SQL("CREATE ROLE")
            conn.execute(
                sql.SQL("{} {} WITH " + _ROLE_ATTRS + " PASSWORD {}").format(
                    verb, sql.Identifier(role.value), sql.Literal(password)
                )
            )


def create_database(settings: Settings, db_name: str, *, drop_existing: bool = False) -> None:
    with psycopg.connect(**settings.superuser_conninfo(), autocommit=True) as conn:  # type: ignore[arg-type]
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,)).fetchone()
        if exists and drop_existing:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(db_name)))
            exists = None
        if not exists:
            conn.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(db_name), sql.Identifier(DbRole.OWNER.value)
                )
            )
        conn.execute(
            sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(db_name))
        )
        for role in RUNTIME_ROLES:
            conn.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(db_name), sql.Identifier(role.value)
                )
            )


def bootstrap(settings: Settings, db_name: str, *, drop_existing: bool = False) -> None:
    ensure_roles(settings)
    create_database(settings, db_name, drop_existing=drop_existing)
