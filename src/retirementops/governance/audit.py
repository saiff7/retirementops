"""Append-only, hash-chained audit log (ADR-009). Tamper-EVIDENT, not immutable.

Runs as `app_service`, which can only INSERT and SELECT `app.audit_events`; a trigger also blocks
UPDATE/DELETE/TRUNCATE for every role. A superuser or the table owner can still disable the trigger and
rewrite history; `verify_chain` then reports the first broken link. That is the honest limit.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Column,
    Connection,
    DateTime,
    Engine,
    FetchedValue,
    MetaData,
    String,
    Table,
    insert,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

from retirementops.clock import Clock, utc_now
from retirementops.domain.evidence import canonical_json, sha256_hex
from retirementops.governance.pseudonym import RAW_IDENTIFIER

app_metadata = MetaData(schema="app")
audit_events = Table(
    "audit_events",
    app_metadata,
    Column(
        "seq", BigInteger, primary_key=True, autoincrement=False, server_default=FetchedValue()
    ),  # GENERATED ALWAYS AS IDENTITY in the DB
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("actor_principal", String, nullable=False),
    Column("actor_role", String),
    Column("tenant_id", String),
    Column("correlation_id", String),
    Column("event_type", String, nullable=False),
    Column("target_ref", String),
    Column("details", JSONB, nullable=False),
    Column("prev_hash", String, nullable=False),
    Column("hash", String, nullable=False),
    implicit_returning=False,
)

GENESIS_HASH = "0" * 64
_AUDIT_LOCK_KEY = 0x5245_5449_4F50  # constant advisory-lock key serialising chain appends
_DETAIL_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
# Raw content never goes into audit details; only ids, hashes, counts and reasons.
_FORBIDDEN_DETAIL_PARTS = frozenset(
    {"body", "text", "content", "token", "password", "secret", "email", "ssn", "subject", "raw"}
)


def _check_details(details: Mapping[str, Any], depth: int = 0) -> None:
    if depth > 3:
        raise ValueError("audit details nested too deeply")
    for key, value in details.items():
        if not _DETAIL_KEY.match(key):
            raise ValueError(f"invalid audit detail key {key!r}")
        if _FORBIDDEN_DETAIL_PARTS & set(key.split("_")):
            raise ValueError(f"audit details may not contain raw content ({key!r})")
        if isinstance(value, str) and len(value) > 500:
            raise ValueError("audit detail value too long")
        if isinstance(value, Mapping):
            _check_details(value, depth + 1)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    actor_principal: str
    event_type: str
    actor_role: str | None = None
    tenant_id: str | None = None
    correlation_id: str | None = None
    target_ref: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


def _hash_body(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "occurred_at": row["occurred_at"],
        "actor_principal": row["actor_principal"],
        "actor_role": row["actor_role"],
        "tenant_id": row["tenant_id"],
        "correlation_id": row["correlation_id"],
        "event_type": row["event_type"],
        "target_ref": row["target_ref"],
        "details": dict(row["details"]),
    }


def chain_hash(prev_hash: str, row: Mapping[str, Any]) -> str:
    return sha256_hex(prev_hash + canonical_json(_hash_body(row)))


@dataclass(frozen=True, slots=True)
class ChainReport:
    ok: bool
    checked: int
    first_bad_seq: int | None
    reason: str | None


def verify_chain(conn: Connection) -> ChainReport:
    prev = GENESIS_HASH
    checked = 0
    for row in conn.execute(select(audit_events).order_by(audit_events.c.seq)).mappings():
        checked += 1
        if row["prev_hash"] != prev:
            return ChainReport(False, checked, int(row["seq"]), "prev_hash does not link")
        if chain_hash(prev, dict(row)) != row["hash"]:
            return ChainReport(False, checked, int(row["seq"]), "row content does not match hash")
        prev = row["hash"]
    return ChainReport(True, checked, None, None)


class AuditLog:
    def __init__(self, engine: Engine, clock: Clock = utc_now) -> None:
        self._engine, self._clock = engine, clock

    def append(self, event: AuditEvent) -> str:
        _check_details(event.details)
        # Hard guarantee: raw participant/employee refs or SSN-like values never reach storage.
        # Callers pseudonymise first (services.audit_service); this refuses anything they missed.
        probe = canonical_json(
            {
                "actor": event.actor_principal,
                "role": event.actor_role,
                "tenant": event.tenant_id,
                "correlation_id": event.correlation_id,
                "type": event.event_type,
                "target": event.target_ref,
                "details": dict(event.details),
            }
        )
        if RAW_IDENTIFIER.search(probe):
            raise ValueError("audit event contains a raw identifier; pseudonymise it first")
        occurred_at: datetime = self._clock()
        with self._engine.begin() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _AUDIT_LOCK_KEY})
            prev = (
                conn.execute(
                    select(audit_events.c.hash).order_by(audit_events.c.seq.desc()).limit(1)
                ).scalar_one_or_none()
                or GENESIS_HASH
            )
            row = {
                "occurred_at": occurred_at,
                "actor_principal": event.actor_principal,
                "actor_role": event.actor_role,
                "tenant_id": event.tenant_id,
                "correlation_id": event.correlation_id,
                "event_type": event.event_type,
                "target_ref": event.target_ref,
                "details": dict(event.details),
            }
            digest = chain_hash(prev, row)
            conn.execute(insert(audit_events).values(**row, prev_hash=prev, hash=digest))
        return digest

    def verify(self) -> ChainReport:
        with self._engine.connect() as conn:
            return verify_chain(conn)
