"""Mock user directory (`app.users`). Authoritative for roles and tenant assignments (ADR-008)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Boolean, Column, Engine, MetaData, String, Table, select
from sqlalchemy.dialects.postgresql import ARRAY

app_metadata = MetaData(schema="app")
users = Table(
    "users",
    app_metadata,
    Column("user_id", String, primary_key=True),
    Column("display_name", String, nullable=False),
    Column("kind", String, nullable=False),
    Column("roles", ARRAY(String), nullable=False),
    Column("tenant_ids", ARRAY(String), nullable=False),
    Column("active", Boolean, nullable=False),
)


@dataclass(frozen=True, slots=True)
class UserRecord:
    user_id: str
    kind: str
    roles: tuple[str, ...]
    tenant_ids: tuple[str, ...]
    active: bool


class UserDirectory:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self, user_id: str) -> UserRecord | None:
        with self._engine.connect() as conn:
            r = conn.execute(select(users).where(users.c.user_id == user_id)).mappings().first()
        if r is None:
            return None
        return UserRecord(
            user_id=str(r["user_id"]),
            kind=str(r["kind"]),
            roles=tuple(r["roles"]),
            tenant_ids=tuple(r["tenant_ids"]),
            active=bool(r["active"]),
        )
