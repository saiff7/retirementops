"""Payroll MOCK adapter. Schema `payroll_mock`.

Reader runs as `svc_payroll_reader` (SELECT only). Ingestor runs as `svc_payroll_ingest`
(INSERT files/rows only). Neither can update or delete a contribution row.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Engine,
    FetchedValue,
    Integer,
    MetaData,
    String,
    Table,
    and_,
    insert,
    select,
)

from retirementops.connectors._scoped import Clock, make_source, scoped_tx, utc_now
from retirementops.domain.authz import TenantScope
from retirementops.domain.ids import payroll_file_id
from retirementops.domain.money import Money
from retirementops.domain.payroll_upload import ParsedPayrollRow
from retirementops.domain.records import PayPeriod, PayrollRow

SYSTEM = "payroll_mock"
metadata = MetaData(schema=SYSTEM)

pay_periods = Table(
    "pay_periods",
    metadata,
    Column("pay_period_id", String, primary_key=True),
    Column("period_start", Date, nullable=False),
    Column("period_end", Date, nullable=False),
    Column("pay_date", Date, nullable=False),
)
payroll_files = Table(
    "payroll_files",
    metadata,
    Column("file_id", String, primary_key=True),
    Column("tenant_id", String, nullable=False),
    Column("plan_id", String, nullable=False),
    Column("pay_period_id", String, nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    Column("file_sha256", String, nullable=False),
    Column("row_count", Integer, nullable=False),
    Column("source", String, nullable=False),
    implicit_returning=False,
)
payroll_rows = Table(
    "payroll_rows",
    metadata,
    Column(
        "row_id", BigInteger, primary_key=True, autoincrement=False, server_default=FetchedValue()
    ),  # GENERATED ALWAYS AS IDENTITY in the DB
    Column("file_id", String, nullable=False),
    Column("tenant_id", String, nullable=False),
    Column("plan_id", String, nullable=False),
    Column("pay_period_id", String, nullable=False),
    Column("participant_ref", String, nullable=False),
    Column("eligible_pay_cents", BigInteger, nullable=False),
    Column("employee_contribution_cents", BigInteger, nullable=False),
    Column("employer_contribution_cents", BigInteger, nullable=False),
    Column("currency", String, nullable=False),
    implicit_returning=False,
)


def _d(v: object) -> date:
    if not isinstance(v, date):
        raise TypeError("expected date")
    return v


def _int(v: object) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise TypeError("expected an integer column")
    return v


class PayrollMockReader:
    def __init__(self, engine: Engine, clock: Clock = utc_now) -> None:
        self._engine, self._clock = engine, clock

    def get_pay_period(self, pay_period_id: str) -> PayPeriod | None:
        # Pay periods are a shared calendar, not tenant data; an empty scope is fine.
        with scoped_tx(self._engine, TenantScope(frozenset())) as conn:
            r = (
                conn.execute(
                    select(pay_periods).where(pay_periods.c.pay_period_id == pay_period_id)
                )
                .mappings()
                .first()
            )
        if r is None:
            return None
        row = dict(r)
        return PayPeriod(
            pay_period_id=str(row["pay_period_id"]),
            period_start=_d(row["period_start"]),
            period_end=_d(row["period_end"]),
            pay_date=_d(row["pay_date"]),
            source=make_source(
                system=SYSTEM,
                record_type="pay_period",
                record_id=str(row["pay_period_id"]),
                record_version=1,
                payload=row,
                clock=self._clock,
            ),
        )

    def list_payroll_rows(
        self, scope: TenantScope, *, participant_ref: str, plan_id: str, pay_period_id: str
    ) -> list[PayrollRow]:
        stmt = (
            select(payroll_rows)
            .where(
                and_(
                    payroll_rows.c.participant_ref == participant_ref,
                    payroll_rows.c.plan_id == plan_id,
                    payroll_rows.c.pay_period_id == pay_period_id,
                    payroll_rows.c.tenant_id.in_(sorted(scope.tenant_ids)),
                )
            )
            .order_by(payroll_rows.c.row_id)
        )
        with scoped_tx(self._engine, scope) as conn:
            rows = [dict(r) for r in conn.execute(stmt).mappings().all()]
        return [self._row(r) for r in rows]

    def get_payroll_row(self, scope: TenantScope, file_id: str, row_id: int) -> PayrollRow | None:
        stmt = select(payroll_rows).where(
            and_(
                payroll_rows.c.row_id == row_id,
                payroll_rows.c.file_id == file_id,
                payroll_rows.c.tenant_id.in_(sorted(scope.tenant_ids)),
            )
        )
        with scoped_tx(self._engine, scope) as conn:
            r = conn.execute(stmt).mappings().first()
        return self._row(dict(r)) if r else None

    def _row(self, r: dict[str, object]) -> PayrollRow:
        return PayrollRow(
            row_id=_int(r["row_id"]),
            file_id=str(r["file_id"]),
            participant_ref=str(r["participant_ref"]),
            plan_id=str(r["plan_id"]),
            tenant_id=str(r["tenant_id"]),
            pay_period_id=str(r["pay_period_id"]),
            eligible_pay=Money.from_cents(_int(r["eligible_pay_cents"])),
            employee_contribution=Money.from_cents(_int(r["employee_contribution_cents"])),
            employer_contribution=Money.from_cents(_int(r["employer_contribution_cents"])),
            source=make_source(
                system=SYSTEM,
                record_type="payroll_row",
                record_id=f"{r['file_id']}#{r['row_id']}",
                record_version=1,
                payload=r,
                clock=self._clock,
            ),
        )


class PayrollMockIngestor:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def ingest_file(
        self,
        scope: TenantScope,
        *,
        tenant_id: str,
        plan_id: str,
        pay_period_id: str,
        file_sha256: str,
        rows: Sequence[ParsedPayrollRow],
        received_at: datetime,
    ) -> str:
        if not scope.allows(tenant_id):
            raise PermissionError("tenant outside scope")
        file_id = payroll_file_id(tenant_id, plan_id, pay_period_id, file_sha256)
        with scoped_tx(self._engine, scope) as conn:  # one transaction: all rows or none
            conn.execute(
                insert(payroll_files).values(
                    file_id=file_id,
                    tenant_id=tenant_id,
                    plan_id=plan_id,
                    pay_period_id=pay_period_id,
                    received_at=received_at,
                    file_sha256=file_sha256,
                    row_count=len(rows),
                    source="upload",
                )
            )
            if rows:
                conn.execute(
                    insert(payroll_rows),
                    [
                        {
                            "file_id": file_id,
                            "tenant_id": tenant_id,
                            "plan_id": plan_id,
                            "pay_period_id": pay_period_id,
                            "participant_ref": r.participant_ref,
                            "eligible_pay_cents": r.eligible_pay.to_cents(),
                            "employee_contribution_cents": r.employee_contribution.to_cents(),
                            "employer_contribution_cents": r.employer_contribution.to_cents(),
                            "currency": "USD",
                        }
                        for r in rows
                    ],
                )
        return file_id
