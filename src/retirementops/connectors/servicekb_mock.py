"""Service-ops policy knowledge base MOCK adapter. Schema `servicekb_mock`, role `svc_servicekb_reader`.

Provider-wide content, so it has no tenant column and no RLS. Read only. Retrieval returns only
versions effective on the requested date; superseded and future versions are never returned.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    Engine,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    and_,
    or_,
    select,
)

from retirementops.connectors._scoped import Clock, make_source, scoped_tx, utc_now
from retirementops.domain.authz import TenantScope
from retirementops.domain.records import PolicyCategory, PolicySnippet

SYSTEM = "servicekb_mock"
metadata = MetaData(schema=SYSTEM)

policy_snippets = Table(
    "policy_snippets",
    metadata,
    Column("snippet_id", String, primary_key=True),
    Column("category", String, nullable=False),
    Column("title", String, nullable=False),
    Column("body", Text, nullable=False),
    Column("version", String, nullable=False),
    Column("effective_from", Date, nullable=False),
    Column("effective_to", Date),
    Column("is_fictional", Boolean, nullable=False),
    Column("record_version", Integer, nullable=False),
)
_NO_TENANT = TenantScope(frozenset())
_MAX_QUERY = 200


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _d(v: object) -> date:
    if not isinstance(v, date):
        raise TypeError("expected date")
    return v


class PolicyKbMockReader:
    def __init__(self, engine: Engine, clock: Clock = utc_now) -> None:
        self._engine, self._clock = engine, clock

    def _snippet(self, r: dict[str, object]) -> PolicySnippet:
        return PolicySnippet(
            snippet_id=str(r["snippet_id"]),
            category=PolicyCategory(str(r["category"])),
            title=str(r["title"]),
            body=str(r["body"]),
            version=str(r["version"]),
            effective_from=_d(r["effective_from"]),
            effective_to=_d(r["effective_to"]) if r["effective_to"] else None,
            source=make_source(
                system=SYSTEM,
                record_type="policy_snippet",
                record_id=str(r["snippet_id"]),
                record_version=r["record_version"],
                payload=r,
                clock=self._clock,
            ),
        )

    def search_snippets(
        self, *, query: str | None, category: PolicyCategory | None, on: date, limit: int
    ) -> list[PolicySnippet]:
        if not 1 <= limit <= 10:
            raise ValueError("limit must be between 1 and 10")
        t = policy_snippets
        stmt = select(t).where(
            and_(t.c.effective_from <= on, or_(t.c.effective_to.is_(None), t.c.effective_to > on))
        )
        if category is not None:
            stmt = stmt.where(t.c.category == category.value)
        if query:
            pattern = f"%{_escape_like(query[:_MAX_QUERY])}%"
            stmt = stmt.where(
                or_(t.c.title.ilike(pattern, escape="\\"), t.c.body.ilike(pattern, escape="\\"))
            )
        stmt = stmt.order_by(t.c.category, t.c.snippet_id).limit(limit)
        with scoped_tx(self._engine, _NO_TENANT) as conn:
            rows = conn.execute(stmt).mappings().all()
        return [self._snippet(dict(r)) for r in rows]

    def get_snippet(self, snippet_id: str) -> PolicySnippet | None:
        with scoped_tx(self._engine, _NO_TENANT) as conn:
            r = (
                conn.execute(
                    select(policy_snippets).where(policy_snippets.c.snippet_id == snippet_id)
                )
                .mappings()
                .first()
            )
        return self._snippet(dict(r)) if r else None
