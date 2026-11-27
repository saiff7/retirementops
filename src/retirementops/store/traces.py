"""Agent run traces (migration 0007). Written only as `svc_agent_recorder`, under RLS.

A finished run is written ONCE: its header, tool calls and evidence items in a single transaction.
The database refuses trace rows outside that transaction, so a stored run cannot gain or lose
entries afterwards. This module has no update or delete path. Tool arguments and results are stored
only as SHA-256 hashes and sizes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Engine,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    and_,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB

from retirementops.db.scoped import scoped_tx
from retirementops.domain.authz import TenantScope

trace_metadata = MetaData(schema="app")
agent_runs = Table(
    "agent_runs",
    trace_metadata,
    Column("run_id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("ticket_id", String, nullable=False),
    Column("started_by", String, nullable=False),
    Column("correlation_id", String, nullable=False),
    Column("skill_name", String, nullable=False),
    Column("skill_version", String, nullable=False),
    Column("skill_sha256", String, nullable=False),
    Column("provider", String, nullable=False),
    Column("model_name", String, nullable=False),
    Column("model_digest", String),
    Column("final_state", String, nullable=False),
    Column("state_path", JSONB, nullable=False),
    Column("state_reasons", JSONB, nullable=False),
    Column("intent", String),
    Column("confidence", Numeric(4, 3)),
    Column("routed_queue", String),
    Column("routing_rules_version", String),
    Column("requires_manual_review", Boolean, nullable=False),
    Column("finding_status", String),
    Column("draft_id", String),
    Column("proposal_id", String),
    Column("model_turns", Integer, nullable=False),
    Column("tool_call_count", Integer, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=False),
    implicit_returning=False,
)
tool_calls = Table(
    "tool_calls",
    trace_metadata,
    Column("run_id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("seq", Integer, primary_key=True),
    Column("tool_name", String, nullable=False),
    Column("decision", String, nullable=False),
    Column("outcome", String, nullable=False),
    Column("args_sha256", String, nullable=False),
    Column("args_bytes", Integer, nullable=False),
    Column("result_sha256", String),
    Column("latency_ms", Numeric(10, 2), nullable=False),
    Column("called_at", DateTime(timezone=True), nullable=False),
    implicit_returning=False,
)
evidence_items = Table(
    "evidence_items",
    trace_metadata,
    Column("run_id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("seq", Integer, primary_key=True),
    Column("system", String, nullable=False),
    Column("record_type", String, nullable=False),
    Column("record_id", String, nullable=False),
    Column("record_version", String, nullable=False),
    Column("content_hash", String, nullable=False),
    Column("retrieved_at", DateTime(timezone=True), nullable=False),
    implicit_returning=False,
)


@dataclass(frozen=True, slots=True)
class RunHeader:
    run_id: str
    tenant_id: str
    ticket_id: str
    started_by: str
    correlation_id: str
    skill_name: str
    skill_version: str
    skill_sha256: str
    provider: str
    model_name: str
    model_digest: str | None
    final_state: str
    state_path: tuple[str, ...]
    state_reasons: tuple[str, ...]
    intent: str | None
    confidence: Decimal | None
    routed_queue: str | None
    routing_rules_version: str | None
    requires_manual_review: bool
    finding_status: str | None
    draft_id: str | None
    proposal_id: str | None
    model_turns: int
    started_at: datetime
    finished_at: datetime


@dataclass(frozen=True, slots=True)
class ToolCallTrace:
    seq: int
    tool_name: str
    decision: str  # allow | deny | error
    outcome: str
    args_sha256: str
    args_bytes: int
    result_sha256: str | None
    latency_ms: Decimal
    called_at: datetime


@dataclass(frozen=True, slots=True)
class EvidenceTrace:
    seq: int
    system: str
    record_type: str
    record_id: str
    record_version: str
    content_hash: str
    retrieved_at: datetime


@dataclass(frozen=True, slots=True)
class StoredRun:
    header: dict[str, Any]
    tool_calls: list[dict[str, Any]]
    evidence: list[dict[str, Any]]


class RunTraceStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine  # must be the svc_agent_recorder engine

    def write_run(
        self,
        header: RunHeader,
        calls: Sequence[ToolCallTrace],
        evidence: Sequence[EvidenceTrace],
    ) -> None:
        scope = TenantScope(frozenset({header.tenant_id}))
        row = {f: getattr(header, f) for f in RunHeader.__slots__}
        row["state_path"], row["state_reasons"] = (
            list(header.state_path),
            list(header.state_reasons),
        )
        row["tool_call_count"] = len(calls)
        with scoped_tx(self._engine, scope) as conn:
            conn.execute(agent_runs.insert().values(**row))
            if calls:
                conn.execute(
                    tool_calls.insert(),
                    [
                        {"run_id": header.run_id, "tenant_id": header.tenant_id}
                        | {f: getattr(c, f) for f in ToolCallTrace.__slots__}
                        for c in calls
                    ],
                )
            if evidence:
                conn.execute(
                    evidence_items.insert(),
                    [
                        {"run_id": header.run_id, "tenant_id": header.tenant_id}
                        | {f: getattr(e, f) for f in EvidenceTrace.__slots__}
                        for e in evidence
                    ],
                )

    def get_run(self, scope: TenantScope, run_id: str) -> StoredRun | None:
        with scoped_tx(self._engine, scope) as conn:
            head = (
                conn.execute(
                    select(agent_runs).where(
                        and_(
                            agent_runs.c.run_id == run_id,
                            agent_runs.c.tenant_id.in_(sorted(scope.tenant_ids)),
                        )
                    )
                )
                .mappings()
                .first()
            )
            if head is None:
                return None
            calls = conn.execute(
                select(tool_calls).where(tool_calls.c.run_id == run_id).order_by(tool_calls.c.seq)
            ).mappings()
            ev = conn.execute(
                select(evidence_items)
                .where(evidence_items.c.run_id == run_id)
                .order_by(evidence_items.c.seq)
            ).mappings()
            return StoredRun(dict(head), [dict(c) for c in calls], [dict(e) for e in ev])
