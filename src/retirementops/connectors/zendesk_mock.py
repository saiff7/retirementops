"""Zendesk MOCK adapter. Not a real Zendesk integration. Schema `zendesk_mock`.

Reader runs as `svc_zendesk_reader`. Writer runs as `svc_zendesk_writer`, whose grants allow exactly:
insert comments and events, and update assignee_queue/updated_at/version. Not status (ADR-003, ADR-007).
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Engine,
    FetchedValue,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    and_,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert

from retirementops.connectors._scoped import Clock, make_source, scoped_tx, utc_now
from retirementops.connectors.interfaces import NotVisibleError, PostedComment, StaleRecordError
from retirementops.domain.authz import TenantScope
from retirementops.domain.records import Ticket
from retirementops.domain.taxonomy import Queue, TicketStatus

SYSTEM = "zendesk_mock"
metadata = MetaData(schema=SYSTEM)

organizations = Table(
    "organizations",
    metadata,
    Column("org_id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("name", String, nullable=False),
)
tickets = Table(
    "tickets",
    metadata,
    Column("ticket_id", String, primary_key=True),
    Column("org_id", String, nullable=False),
    Column("tenant_id", String, nullable=False),
    Column("requester_email", String, nullable=False),
    Column("subject", String, nullable=False),
    Column("body", Text, nullable=False),
    Column("status", String, nullable=False),
    Column("priority", String, nullable=False),
    Column("assignee_queue", String),
    Column("channel", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("version", Integer, nullable=False),
)
ticket_comments = Table(
    "ticket_comments",
    metadata,
    Column(
        "comment_id",
        BigInteger,
        primary_key=True,
        autoincrement=False,
        server_default=FetchedValue(),
    ),  # GENERATED ALWAYS AS IDENTITY in the DB
    Column("ticket_id", String, nullable=False),
    Column("tenant_id", String, nullable=False),
    Column("author_kind", String, nullable=False),
    Column("public", Boolean, nullable=False),
    Column("body", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("idempotency_key", String),
    implicit_returning=False,
)
ticket_events = Table(
    "ticket_events",
    metadata,
    Column(
        "event_id", BigInteger, primary_key=True, autoincrement=False, server_default=FetchedValue()
    ),  # GENERATED ALWAYS AS IDENTITY in the DB
    Column("ticket_id", String, nullable=False),
    Column("tenant_id", String, nullable=False),
    Column("event_type", String, nullable=False),
    Column("actor_kind", String, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("details", JSONB, nullable=False),
    implicit_returning=False,
)

_MAX_QUERY = 200


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _to_ticket(row: dict[str, object], clock: Clock) -> Ticket:
    payload = {c.name: row[c.name] for c in tickets.c}
    return Ticket(
        ticket_id=str(row["ticket_id"]),
        tenant_id=str(row["tenant_id"]),
        org_id=str(row["org_id"]),
        subject=str(row["subject"]),
        body=str(row["body"]),
        status=TicketStatus(str(row["status"])),
        priority=str(row["priority"]),
        assignee_queue=Queue(str(row["assignee_queue"])) if row["assignee_queue"] else None,
        channel=str(row["channel"]),
        created_at=row["created_at"],  # type: ignore[arg-type]
        updated_at=row["updated_at"],  # type: ignore[arg-type]
        version=int(row["version"]),  # type: ignore[call-overload]
        source=make_source(
            system=SYSTEM,
            record_type="ticket",
            record_id=str(row["ticket_id"]),
            record_version=row["version"],
            payload=payload,
            clock=clock,
        ),
    )


class ZendeskMockReader:
    def __init__(self, engine: Engine, clock: Clock = utc_now) -> None:
        self._engine, self._clock = engine, clock

    def search_tickets(
        self,
        scope: TenantScope,
        *,
        query: str | None,
        status: TicketStatus | None,
        priority: str | None,
        limit: int,
    ) -> list[Ticket]:
        if not 1 <= limit <= 25:
            raise ValueError("limit must be between 1 and 25")
        stmt = select(tickets).where(tickets.c.tenant_id.in_(sorted(scope.tenant_ids)))
        if query:
            stmt = stmt.where(
                tickets.c.subject.ilike(f"%{_escape_like(query[:_MAX_QUERY])}%", escape="\\")
            )
        if status is not None:
            stmt = stmt.where(tickets.c.status == status.value)
        if priority is not None:
            stmt = stmt.where(tickets.c.priority == priority)
        stmt = stmt.order_by(tickets.c.created_at.desc(), tickets.c.ticket_id).limit(limit)
        with scoped_tx(self._engine, scope) as conn:
            rows = conn.execute(stmt).mappings().all()
        return [_to_ticket(dict(r), self._clock) for r in rows]

    def get_ticket(self, scope: TenantScope, ticket_id: str) -> Ticket | None:
        stmt = select(tickets).where(
            and_(
                tickets.c.ticket_id == ticket_id, tickets.c.tenant_id.in_(sorted(scope.tenant_ids))
            )
        )
        with scoped_tx(self._engine, scope) as conn:
            row = conn.execute(stmt).mappings().first()
        return _to_ticket(dict(row), self._clock) if row else None


class ZendeskMockWriter:
    """Two narrow writes. Called only by the execution service after human approval (M5)."""

    def __init__(self, engine: Engine, clock: Clock = utc_now) -> None:
        self._engine, self._clock = engine, clock

    def post_public_reply(
        self,
        scope: TenantScope,
        *,
        ticket_id: str,
        body: str,
        idempotency_key: str,
        expected_version: int,
    ) -> PostedComment:
        if not 1 <= len(body) <= 20_000:
            raise ValueError("reply body length out of range")
        if not 8 <= len(idempotency_key) <= 200:
            raise ValueError("idempotency key length out of range")
        now = self._clock()
        with scoped_tx(self._engine, scope) as conn:
            existing = (
                conn.execute(
                    select(ticket_comments).where(
                        ticket_comments.c.idempotency_key == idempotency_key
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                if existing["ticket_id"] != ticket_id:
                    raise ValueError("idempotency key already used for a different ticket")
                return PostedComment(
                    int(existing["comment_id"]),
                    ticket_id,
                    idempotency_key,
                    existing["created_at"],
                    replayed=True,
                )
            ticket = (
                conn.execute(
                    select(tickets.c.tenant_id, tickets.c.version)
                    .where(
                        and_(
                            tickets.c.ticket_id == ticket_id,
                            tickets.c.tenant_id.in_(sorted(scope.tenant_ids)),
                        )
                    )
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if ticket is None:
                raise NotVisibleError(ticket_id)
            if ticket["version"] != expected_version:
                raise StaleRecordError(ticket_id)
            inserted = conn.execute(
                pg_insert(ticket_comments)
                .values(
                    ticket_id=ticket_id,
                    tenant_id=ticket["tenant_id"],
                    author_kind="retirementops_service",
                    public=True,
                    body=body,
                    created_at=now,
                    idempotency_key=idempotency_key,
                )
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
                .returning(ticket_comments.c.comment_id)
            ).first()
            if inserted is None:  # lost a race with an identical request
                raise StaleRecordError(ticket_id)
            conn.execute(
                update(tickets)
                .where(
                    and_(tickets.c.ticket_id == ticket_id, tickets.c.version == expected_version)
                )
                .values(updated_at=now, version=expected_version + 1)
            )
            conn.execute(
                insert(ticket_events).values(
                    ticket_id=ticket_id,
                    tenant_id=ticket["tenant_id"],
                    event_type="comment.added",
                    actor_kind="retirementops_service",
                    occurred_at=now,
                    details={"comment_id": inserted[0]},
                )
            )
        return PostedComment(int(inserted[0]), ticket_id, idempotency_key, now, replayed=False)

    def apply_assignment(
        self, scope: TenantScope, *, ticket_id: str, queue: Queue, expected_version: int
    ) -> Ticket:
        now = self._clock()
        with scoped_tx(self._engine, scope) as conn:
            row = (
                conn.execute(
                    update(tickets)
                    .where(
                        and_(
                            tickets.c.ticket_id == ticket_id,
                            tickets.c.tenant_id.in_(sorted(scope.tenant_ids)),
                            tickets.c.version == expected_version,
                        )
                    )
                    .values(
                        assignee_queue=queue.value, updated_at=now, version=expected_version + 1
                    )
                    .returning(*tickets.c)
                )
                .mappings()
                .first()
            )
            if row is None:
                exists = conn.execute(
                    select(tickets.c.ticket_id).where(tickets.c.ticket_id == ticket_id)
                ).first()
                raise StaleRecordError(ticket_id) if exists else NotVisibleError(ticket_id)
            conn.execute(
                insert(ticket_events).values(
                    ticket_id=ticket_id,
                    tenant_id=row["tenant_id"],
                    event_type="assignment.changed",
                    actor_kind="retirementops_service",
                    occurred_at=now,
                    details={"queue": queue.value},
                )
            )
        return _to_ticket(dict(row), self._clock)
