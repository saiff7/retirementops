"""Plan administration MOCK adapter (read only). Schema `planadmin_mock`, role `svc_planadmin_reader`."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Column,
    ColumnElement,
    Date,
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

from retirementops.connectors._scoped import Clock, make_source, scoped_tx, utc_now
from retirementops.domain.authz import TenantScope
from retirementops.domain.evidence import SourceRef
from retirementops.domain.records import (
    ContributionType,
    Enrollment,
    EnrollmentStatus,
    Participant,
    Plan,
    PlanRule,
    Tenant,
)

SYSTEM = "planadmin_mock"
metadata = MetaData(schema=SYSTEM)

tenants = Table(
    "tenants",
    metadata,
    Column("tenant_id", String, primary_key=True),
    Column("display_name", String, nullable=False),
    Column("record_version", Integer, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
plans = Table(
    "plans",
    metadata,
    Column("plan_id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("name", String, nullable=False),
    Column("currency", String, nullable=False),
    Column("record_version", Integer, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
plan_rules = Table(
    "plan_rules",
    metadata,
    Column("rule_id", String, primary_key=True),
    Column("plan_id", String, nullable=False),
    Column("tenant_id", String, nullable=False),
    Column("contribution_type", String, nullable=False),
    Column("rule_version", String, nullable=False),
    Column("formula_code", String, nullable=False),
    Column("max_deferral_pct", Numeric(7, 4, asdecimal=True)),
    Column("match_rate", Numeric(7, 4, asdecimal=True)),
    Column("match_cap_pct", Numeric(7, 4, asdecimal=True)),
    Column("rounding", String, nullable=False),
    Column("effective_from", Date, nullable=False),
    Column("effective_to", Date),
    Column("is_fictional", Boolean, nullable=False),
    Column("record_version", Integer, nullable=False),
)
participants = Table(
    "participants",
    metadata,
    Column("participant_ref", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("employer_employee_ref", String, nullable=False),
    Column("record_version", Integer, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
enrollments = Table(
    "enrollments",
    metadata,
    Column("enrollment_id", String, primary_key=True),
    Column("participant_ref", String, nullable=False),
    Column("plan_id", String, nullable=False),
    Column("tenant_id", String, nullable=False),
    Column("status", String, nullable=False),
    Column("deferral_pct", Numeric(7, 4, asdecimal=True), nullable=False),
    Column("effective_from", Date, nullable=False),
    Column("effective_to", Date),
    Column("record_version", Integer, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


def _dec(v: object) -> Decimal | None:
    if v is None:
        return None
    if not isinstance(v, Decimal):
        raise TypeError("expected Decimal from NUMERIC column")
    return v


def _date(v: object) -> date:
    if not isinstance(v, date):
        raise TypeError("expected date")
    return v


class PlanAdminMockReader:
    def __init__(self, engine: Engine, clock: Clock = utc_now) -> None:
        self._engine, self._clock = engine, clock

    def _src(self, record_type: str, record_id: str, row: dict[str, object]) -> SourceRef:
        return make_source(
            system=SYSTEM,
            record_type=record_type,
            record_id=record_id,
            record_version=row["record_version"],
            payload=row,
            clock=self._clock,
        )

    def _in_scope(self, table: Table, scope: TenantScope) -> ColumnElement[bool]:
        return table.c.tenant_id.in_(sorted(scope.tenant_ids))

    def get_tenant(self, scope: TenantScope, tenant_id: str) -> Tenant | None:
        stmt = select(tenants).where(
            and_(tenants.c.tenant_id == tenant_id, self._in_scope(tenants, scope))
        )
        with scoped_tx(self._engine, scope) as conn:
            r = conn.execute(stmt).mappings().first()
        if r is None:
            return None
        row = dict(r)
        return Tenant(
            str(row["tenant_id"]),
            str(row["display_name"]),
            self._src("tenant", str(row["tenant_id"]), row),
        )

    def list_plans(self, scope: TenantScope) -> list[Plan]:
        """Plans of the tenants in scope (M3 host resolution). Explicit filter plus RLS."""
        stmt = select(plans).where(self._in_scope(plans, scope)).order_by(plans.c.plan_id)
        with scoped_tx(self._engine, scope) as conn:
            rows = [dict(r) for r in conn.execute(stmt).mappings()]
        return [self._plan(row) for row in rows]

    def get_plan(self, scope: TenantScope, plan_id: str) -> Plan | None:
        stmt = select(plans).where(and_(plans.c.plan_id == plan_id, self._in_scope(plans, scope)))
        with scoped_tx(self._engine, scope) as conn:
            r = conn.execute(stmt).mappings().first()
        if r is None:
            return None
        return self._plan(dict(r))

    def _plan(self, row: dict[str, object]) -> Plan:
        return Plan(
            str(row["plan_id"]),
            str(row["tenant_id"]),
            str(row["name"]),
            str(row["currency"]),
            self._src("plan", str(row["plan_id"]), row),
        )

    def _rule(self, r: dict[str, object]) -> PlanRule:
        return PlanRule(
            rule_id=str(r["rule_id"]),
            plan_id=str(r["plan_id"]),
            tenant_id=str(r["tenant_id"]),
            contribution_type=ContributionType(str(r["contribution_type"])),
            rule_version=str(r["rule_version"]),
            formula_code=str(r["formula_code"]),
            max_deferral_pct=_dec(r["max_deferral_pct"]),
            match_rate=_dec(r["match_rate"]),
            match_cap_pct=_dec(r["match_cap_pct"]),
            rounding=str(r["rounding"]),
            effective_from=_date(r["effective_from"]),
            effective_to=_date(r["effective_to"]) if r["effective_to"] else None,
            source=self._src("plan_rule", str(r["rule_id"]), r),
        )

    def list_plan_rules(self, scope: TenantScope, plan_id: str) -> list[PlanRule]:
        stmt = (
            select(plan_rules)
            .where(and_(plan_rules.c.plan_id == plan_id, self._in_scope(plan_rules, scope)))
            .order_by(
                plan_rules.c.contribution_type, plan_rules.c.effective_from, plan_rules.c.rule_id
            )
        )
        with scoped_tx(self._engine, scope) as conn:
            rows = [dict(r) for r in conn.execute(stmt).mappings().all()]
        return [self._rule(r) for r in rows]

    def get_plan_rule(self, scope: TenantScope, rule_id: str) -> PlanRule | None:
        stmt = select(plan_rules).where(
            and_(plan_rules.c.rule_id == rule_id, self._in_scope(plan_rules, scope))
        )
        with scoped_tx(self._engine, scope) as conn:
            r = conn.execute(stmt).mappings().first()
        return self._rule(dict(r)) if r else None

    def _participant(self, row: dict[str, object]) -> Participant:
        # employer_employee_ref is deliberately not exposed in the DTO (minimisation).
        return Participant(
            str(row["participant_ref"]),
            str(row["tenant_id"]),
            self._src("participant", str(row["participant_ref"]), row),
        )

    def get_participant(self, scope: TenantScope, participant_ref: str) -> Participant | None:
        stmt = select(participants).where(
            and_(
                participants.c.participant_ref == participant_ref,
                self._in_scope(participants, scope),
            )
        )
        with scoped_tx(self._engine, scope) as conn:
            r = conn.execute(stmt).mappings().first()
        return self._participant(dict(r)) if r else None

    def find_participants_by_employee_ref(
        self, scope: TenantScope, employer_employee_ref: str
    ) -> list[Participant]:
        stmt = (
            select(participants)
            .where(
                and_(
                    participants.c.employer_employee_ref == employer_employee_ref,
                    self._in_scope(participants, scope),
                )
            )
            .order_by(participants.c.participant_ref)
        )
        with scoped_tx(self._engine, scope) as conn:
            rows = conn.execute(stmt).mappings().all()
        return [self._participant(dict(r)) for r in rows]

    def list_enrollments(
        self, scope: TenantScope, participant_ref: str, plan_id: str
    ) -> list[Enrollment]:
        stmt = (
            select(enrollments)
            .where(
                and_(
                    enrollments.c.participant_ref == participant_ref,
                    enrollments.c.plan_id == plan_id,
                    self._in_scope(enrollments, scope),
                )
            )
            .order_by(enrollments.c.effective_from, enrollments.c.enrollment_id)
        )
        with scoped_tx(self._engine, scope) as conn:
            rows = [dict(r) for r in conn.execute(stmt).mappings().all()]
        return [self._enrollment(r) for r in rows]

    def get_enrollment(self, scope: TenantScope, enrollment_id: str) -> Enrollment | None:
        stmt = select(enrollments).where(
            and_(enrollments.c.enrollment_id == enrollment_id, self._in_scope(enrollments, scope))
        )
        with scoped_tx(self._engine, scope) as conn:
            r = conn.execute(stmt).mappings().first()
        return self._enrollment(dict(r)) if r else None

    def _enrollment(self, r: dict[str, object]) -> Enrollment:
        pct = _dec(r["deferral_pct"])
        if pct is None:
            raise TypeError("deferral_pct is NOT NULL")
        return Enrollment(
            enrollment_id=str(r["enrollment_id"]),
            participant_ref=str(r["participant_ref"]),
            plan_id=str(r["plan_id"]),
            tenant_id=str(r["tenant_id"]),
            status=EnrollmentStatus(str(r["status"])),
            deferral_pct=pct,
            effective_from=_date(r["effective_from"]),
            effective_to=_date(r["effective_to"]) if r["effective_to"] else None,
            source=self._src("enrollment", str(r["enrollment_id"]), r),
        )
