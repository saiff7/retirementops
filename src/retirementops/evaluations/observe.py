"""Read-only observation of `retirementops_eval` as `svc_eval_reader` (ADR-010 Amendment 1, 1a).

The only evaluation module that uses the reader role. It never writes: the role has SELECT only,
every transaction is also declared READ ONLY, and RLS applies with an explicit tenant scope (the
golden manifest's tenant list).

Accepted blind spot (ACC-15): a write under a tenant outside that list would not be visible here.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Connection, Engine, text

from retirementops.config import DbRole, Settings
from retirementops.db.engines import make_engine
from retirementops.domain.authz import TenantScope

# Every mock-system table, snapshotted before and after each run.
MOCK_TABLES = (
    "zendesk_mock.organizations",
    "zendesk_mock.tickets",
    "zendesk_mock.ticket_comments",
    "zendesk_mock.ticket_events",
    "planadmin_mock.tenants",
    "planadmin_mock.plans",
    "planadmin_mock.plan_rules",
    "planadmin_mock.participants",
    "planadmin_mock.enrollments",
    "payroll_mock.pay_periods",
    "payroll_mock.payroll_files",
    "payroll_mock.payroll_rows",
    "servicekb_mock.policy_snippets",
)
EVAL_DB = "retirementops_eval"


def reader_engine(settings: Settings, db_name: str = EVAL_DB) -> Engine:
    if db_name != EVAL_DB:
        raise ValueError("the scoring reader only reads retirementops_eval")
    return make_engine(settings, DbRole.EVAL_READER, db_name)


@dataclass
class Readback:
    header: dict[str, Any] | None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    draft: dict[str, Any] | None = None  # status, reasons, validator version, created_by
    draft_body: str | None = None  # in memory for scoring only; never reported
    cited: list[dict[str, Any]] = field(default_factory=list)
    proposals: list[dict[str, Any]] = field(default_factory=list)


class Observer:
    def __init__(self, engine: Engine, tenants: tuple[str, ...]) -> None:
        self._engine, self._tenants = engine, tuple(tenants)

    @contextmanager
    def _tx(self, tenants: tuple[str, ...]) -> Iterator[Connection]:
        with self._engine.begin() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            db = conn.execute(text("SELECT current_database()")).scalar_one()
            if db != EVAL_DB:
                raise RuntimeError("observer connected to the wrong database")
            conn.execute(
                text("SELECT set_config('retirementops.tenant_ids', :v, true)"),
                {"v": TenantScope(frozenset(tenants)).as_setting()},
            )
            yield conn

    def snapshot(self) -> dict[str, str]:
        """An order-independent digest of every mock-system table, across all tenants."""
        out: dict[str, str] = {}
        with self._tx(self._tenants) as c:
            for qualified in MOCK_TABLES:  # fixed constants, never caller input
                out[qualified] = c.execute(
                    text(
                        "SELECT count(*)::text || ':' || coalesce(md5(string_agg(t::text, ',' "  # noqa: S608
                        f"ORDER BY t::text)), '') FROM {qualified} t"
                    )
                ).scalar_one()
        return out

    def open_proposals(self, tenant_id: str, ticket_id: str) -> list[dict[str, Any]]:
        with self._tx((tenant_id,)) as c:
            rows = c.execute(
                text(
                    "SELECT proposal_id, target_queue, status, created_by FROM app.action_proposals "
                    "WHERE ticket_id = :t AND status = 'PROPOSED' ORDER BY created_at, proposal_id"
                ),
                {"t": ticket_id},
            ).mappings()
            return [dict(r) for r in rows]

    def readback(
        self, tenant_id: str, run_id: str, draft_id: str | None, ticket_id: str
    ) -> Readback:
        with self._tx((tenant_id,)) as c:
            header = (
                c.execute(text("SELECT * FROM app.agent_runs WHERE run_id = :r"), {"r": run_id})
                .mappings()
                .first()
            )
            calls = c.execute(
                text(
                    "SELECT seq, tool_name, decision, outcome, args_bytes FROM app.tool_calls "
                    "WHERE run_id = :r ORDER BY seq"
                ),
                {"r": run_id},
            ).mappings()
            evidence = c.execute(
                text(
                    "SELECT seq, system, record_type, record_id FROM app.evidence_items "
                    "WHERE run_id = :r ORDER BY seq"
                ),
                {"r": run_id},
            ).mappings()
            rb = Readback(
                header=dict(header) if header else None,
                tool_calls=[dict(r) for r in calls],
                evidence=[dict(r) for r in evidence],
            )
            if draft_id:
                d = (
                    c.execute(
                        text(
                            "SELECT d.status, d.status_reasons, d.validator_version, d.created_by, "
                            "d.created_by_kind, d.ticket_id, d.tenant_id, v.body, v.evidence_refs "
                            "FROM app.drafts d JOIN app.draft_versions v USING (draft_id) "
                            "WHERE d.draft_id = :d ORDER BY v.version DESC LIMIT 1"
                        ),
                        {"d": draft_id},
                    )
                    .mappings()
                    .first()
                )
                if d:
                    rb.draft = {
                        k: d[k]
                        for k in (
                            "status",
                            "status_reasons",
                            "validator_version",
                            "created_by",
                            "created_by_kind",
                            "ticket_id",
                            "tenant_id",
                        )
                    }
                    rb.draft_body = d["body"]
                    rb.cited = list(d["evidence_refs"] or [])
            props = c.execute(
                text(
                    "SELECT proposal_id, target_queue, status, created_by FROM app.action_proposals "
                    "WHERE ticket_id = :t ORDER BY created_at, proposal_id"
                ),
                {"t": ticket_id},
            ).mappings()
            rb.proposals = [dict(r) for r in props]
        return rb

    def record_tenant(self, system: str, record_type: str, record_id: str) -> str | None:
        """Which seeded tenant owns a cited or retrieved record (None if not tenant-owned)."""
        owner = self.record_owner(system, record_type, record_id)
        return None if owner is None else owner.get("tenant_id")

    def record_owner(self, system: str, record_type: str, record_id: str) -> dict[str, Any] | None:
        """Who and what a record belongs to, read from the record itself (review M-2).

        Returns the owning tenant, participant, plan and pay period where the record has them,
        `{"provider_wide": True}` for a policy snippet that exists, `{"pay_period_id": ...}` for a
        pay period, or None when the record does not exist or is not visible."""
        kind = (system.split("_")[0], record_type)
        queries = {
            ("planadmin", "plan"): (
                "SELECT tenant_id, plan_id FROM planadmin_mock.plans WHERE plan_id = :i"
            ),
            ("planadmin", "plan_rule"): (
                "SELECT tenant_id, plan_id FROM planadmin_mock.plan_rules WHERE rule_id = :i"
            ),
            ("planadmin", "enrollment"): (
                "SELECT tenant_id, participant_ref, plan_id FROM planadmin_mock.enrollments "
                "WHERE enrollment_id = :i"
            ),
            ("planadmin", "participant"): (
                "SELECT tenant_id, participant_ref FROM planadmin_mock.participants "
                "WHERE participant_ref = :i"
            ),
            ("zendesk", "ticket"): (
                "SELECT tenant_id, ticket_id FROM zendesk_mock.tickets WHERE ticket_id = :i"
            ),
            ("payroll", "pay_period"): (
                "SELECT pay_period_id FROM payroll_mock.pay_periods WHERE pay_period_id = :i"
            ),
            ("servicekb", "policy_snippet"): (
                "SELECT true AS provider_wide FROM servicekb_mock.policy_snippets "
                "WHERE snippet_id::text = :i"
            ),
        }
        with self._tx(self._tenants) as c:
            if kind == ("payroll", "payroll_row"):
                if "#" not in record_id:
                    return None
                file_id, row_id = record_id.split("#", 1)
                row = (
                    c.execute(
                        text(
                            "SELECT tenant_id, participant_ref, plan_id, pay_period_id "
                            "FROM payroll_mock.payroll_rows WHERE file_id = :f AND row_id::text = :r"
                        ),
                        {"f": file_id, "r": row_id},
                    )
                    .mappings()
                    .first()
                )
            elif kind in queries:
                row = c.execute(text(queries[kind]), {"i": record_id}).mappings().first()
            else:
                return None
        return None if row is None else dict(row)
