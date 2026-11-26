"""RetirementOps workflow metadata: drafts (versioned, hashed) and action proposals.

Runs as `app_service` under RLS (tenant-scoped transactions). INSERT and SELECT only: nothing in this
module can update or delete. Status transitions arrive with the validator (M3) and approvals (M5).
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    Engine,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    and_,
    select,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert

from retirementops.clock import Clock, utc_now
from retirementops.db.scoped import scoped_tx
from retirementops.domain.authz import TenantScope
from retirementops.domain.evidence import content_hash

app_metadata = MetaData(schema="app")
drafts = Table(
    "drafts",
    app_metadata,
    Column("draft_id", String, primary_key=True),
    Column("ticket_id", String, nullable=False),
    Column("tenant_id", String, nullable=False),
    Column("status", String, nullable=False),
    Column("status_reasons", JSONB, nullable=False),
    Column("created_by", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("idempotency_key", String),
    Column("created_by_kind", String, nullable=False),
    implicit_returning=False,
)
draft_versions = Table(
    "draft_versions",
    app_metadata,
    Column("draft_id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("version", Integer, primary_key=True),
    Column("body", Text, nullable=False),
    Column("evidence_refs", JSONB, nullable=False),
    Column("skill_version", String, nullable=False),
    Column("ticket_version", Integer, nullable=False),
    Column("content_hash", String, nullable=False),
    Column("author_principal", String, nullable=False),
    Column("author_kind", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    implicit_returning=False,
)
action_proposals = Table(
    "action_proposals",
    app_metadata,
    Column("proposal_id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("ticket_id", String, nullable=False),
    Column("action_type", String, nullable=False),
    Column("target_queue", String, nullable=False),
    Column("rationale", Text, nullable=False),
    Column("status", String, nullable=False),
    Column("ticket_version", Integer, nullable=False),
    Column("content_hash", String, nullable=False),
    Column("created_by", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    implicit_returning=False,
)

REPLY_ACTION = "POST_PUBLIC_REPLY"


class DraftConflict(Exception):
    """An idempotency key was reused concurrently or with different content."""


ASSIGN_ACTION = "ASSIGN_QUEUE"


def _new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(8).upper()}"


def draft_content_hash(
    *, ticket_id: str, body: str, evidence_refs: Sequence[Mapping[str, Any]]
) -> str:
    """ADR-007: the hash a reviewer must approve. Covers body, evidence, target and action."""
    return content_hash(
        {
            "action": REPLY_ACTION,
            "ticket_id": ticket_id,
            "body": body,
            "evidence_refs": [dict(r) for r in evidence_refs],
        }
    )


def proposal_content_hash(*, ticket_id: str, target_queue: str) -> str:
    """Dedupe key for open proposals. Rationale is excluded: same action = same proposal."""
    return content_hash({"action": ASSIGN_ACTION, "ticket_id": ticket_id, "target": target_queue})


@dataclass(frozen=True, slots=True)
class DraftRecord:
    draft_id: str
    ticket_id: str
    version: int
    content_hash: str
    status: str
    status_reasons: tuple[str, ...]
    created_at: datetime
    replayed: bool = False
    skill_version: str | None = None


@dataclass(frozen=True, slots=True)
class ProposalRecord:
    proposal_id: str
    ticket_id: str
    action_type: str
    target_queue: str
    status: str
    ticket_version: int
    content_hash: str
    created_by: str
    created_at: datetime
    deduplicated: bool = False


def _proposal(r: Mapping[str, Any], *, deduplicated: bool = False) -> ProposalRecord:
    return ProposalRecord(
        proposal_id=str(r["proposal_id"]),
        ticket_id=str(r["ticket_id"]),
        action_type=str(r["action_type"]),
        target_queue=str(r["target_queue"]),
        status=str(r["status"]),
        ticket_version=int(r["ticket_version"]),
        content_hash=str(r["content_hash"]),
        created_by=str(r["created_by"]),
        created_at=r["created_at"],
        deduplicated=deduplicated,
    )


class WorkflowStore:
    def __init__(self, engine: Engine, clock: Clock = utc_now) -> None:
        self._engine, self._clock = engine, clock

    def find_draft_by_idempotency_key(
        self, scope: TenantScope, *, tenant_id: str, created_by: str, idempotency_key: str
    ) -> DraftRecord | None:
        """The caller's OWN original draft for a retried request, with its version-1 hash and
        skill version. Keys are per (tenant, creator): another user's key is never found (N-1)."""
        d, v = drafts, draft_versions
        with scoped_tx(self._engine, scope) as conn:
            r = (
                conn.execute(
                    select(d, v.c.version, v.c.content_hash, v.c.skill_version)
                    .join(v, and_(v.c.draft_id == d.c.draft_id, v.c.version == 1))
                    .where(
                        and_(
                            d.c.tenant_id == tenant_id,
                            d.c.tenant_id.in_(sorted(scope.tenant_ids)),
                            d.c.created_by == created_by,
                            d.c.idempotency_key == idempotency_key,
                        )
                    )
                )
                .mappings()
                .first()
            )
        if r is None:
            return None
        return DraftRecord(
            draft_id=str(r["draft_id"]),
            ticket_id=str(r["ticket_id"]),
            version=int(r["version"]),
            content_hash=str(r["content_hash"]),
            status=str(r["status"]),
            status_reasons=tuple(r["status_reasons"]),
            created_at=r["created_at"],
            replayed=True,
            skill_version=str(r["skill_version"]),
        )

    def create_draft(
        self,
        scope: TenantScope,
        *,
        ticket_id: str,
        tenant_id: str,
        body: str,
        evidence_refs: Sequence[Mapping[str, Any]],
        skill_version: str,
        ticket_version: int,
        author_principal: str,
        author_kind: str,
        status: str,
        status_reasons: Sequence[str],
        idempotency_key: str | None = None,
    ) -> DraftRecord:
        """Header + version 1 in one transaction. The database (migrations 0003/0004) independently
        refuses any status but NEEDS_MANUAL_REVIEW, versions other than 1, and a version-1 author,
        author kind or timestamp that does not match the header."""
        if not scope.allows(tenant_id):
            raise PermissionError("tenant outside scope")
        now = self._clock()
        draft_id = _new_id("DRF")
        digest = draft_content_hash(ticket_id=ticket_id, body=body, evidence_refs=evidence_refs)
        with scoped_tx(self._engine, scope) as conn:
            inserted = conn.execute(
                pg_insert(drafts)
                .values(
                    draft_id=draft_id,
                    ticket_id=ticket_id,
                    tenant_id=tenant_id,
                    status=status,
                    status_reasons=list(status_reasons),
                    created_by=author_principal,
                    created_at=now,
                    idempotency_key=idempotency_key,
                    created_by_kind=author_kind,
                )
                .on_conflict_do_nothing(
                    index_elements=["tenant_id", "created_by", "idempotency_key"],
                    index_where=sql_text("idempotency_key IS NOT NULL"),
                )
                .returning(drafts.c.draft_id)
            ).first()
            if inserted is None:  # lost a race with an identical retry
                raise DraftConflict("idempotency key already used")
            conn.execute(
                draft_versions.insert().values(
                    draft_id=draft_id,
                    tenant_id=tenant_id,
                    version=1,
                    body=body,
                    evidence_refs=[dict(r) for r in evidence_refs],
                    skill_version=skill_version,
                    ticket_version=ticket_version,
                    content_hash=digest,
                    author_principal=author_principal,
                    author_kind=author_kind,
                    created_at=now,
                )
            )
        return DraftRecord(
            draft_id,
            ticket_id,
            1,
            digest,
            status,
            tuple(status_reasons),
            now,
            skill_version=skill_version,
        )

    def create_or_get_proposal(
        self,
        scope: TenantScope,
        *,
        ticket_id: str,
        tenant_id: str,
        target_queue: str,
        rationale: str,
        ticket_version: int,
        created_by: str,
    ) -> ProposalRecord:
        if not scope.allows(tenant_id):
            raise PermissionError("tenant outside scope")
        digest = proposal_content_hash(ticket_id=ticket_id, target_queue=target_queue)
        t = action_proposals
        with scoped_tx(self._engine, scope) as conn:
            row = (
                conn.execute(
                    pg_insert(t)
                    .values(
                        proposal_id=_new_id("PRP"),
                        tenant_id=tenant_id,
                        ticket_id=ticket_id,
                        action_type=ASSIGN_ACTION,
                        target_queue=target_queue,
                        rationale=rationale,
                        status="PROPOSED",
                        ticket_version=ticket_version,
                        content_hash=digest,
                        created_by=created_by,
                        created_at=self._clock(),
                    )
                    .on_conflict_do_nothing(
                        index_elements=["tenant_id", "content_hash"],
                        index_where=sql_text("status = 'PROPOSED'"),
                    )
                    .returning(*t.c)
                )
                .mappings()
                .first()
            )
            if row is not None:
                return _proposal(dict(row))
            existing = (
                conn.execute(
                    select(t).where(
                        and_(
                            t.c.tenant_id == tenant_id,
                            t.c.content_hash == digest,
                            t.c.status == "PROPOSED",
                        )
                    )
                )
                .mappings()
                .one()
            )
            return _proposal(dict(existing), deduplicated=True)

    def get_proposal(self, scope: TenantScope, proposal_id: str) -> ProposalRecord | None:
        t = action_proposals
        with scoped_tx(self._engine, scope) as conn:
            r = (
                conn.execute(
                    select(t).where(
                        and_(
                            t.c.proposal_id == proposal_id,
                            t.c.tenant_id.in_(sorted(scope.tenant_ids)),
                        )
                    )
                )
                .mappings()
                .first()
            )
        return _proposal(dict(r)) if r else None
