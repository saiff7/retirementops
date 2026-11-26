"""Synthetic payroll file ingestion. Rejected files persist NOTHING except an audit record of
{filename hash, reason, row count, file sha256}. Not exposed through the API in M1.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from retirementops.clock import Clock, utc_now
from retirementops.connectors.interfaces import PayrollIngestor, PayrollReader, PlanAdminReader
from retirementops.domain.authz import Scope, TenantScope
from retirementops.domain.payroll_upload import (
    RejectionReason,
    UploadAccepted,
    UploadRejected,
    validate_payroll_csv,
)
from retirementops.services.audit_service import AuditService
from retirementops.services.business import PlanService
from retirementops.services.context import RequestContext
from retirementops.services.errors import NotFound


@dataclass(frozen=True, slots=True)
class IngestResult:
    accepted: bool
    file_id: str | None
    row_count: int
    reason: str | None
    detail: str | None


class PayrollIngestService:
    def __init__(
        self,
        *,
        plans: PlanService,
        planadmin: PlanAdminReader,
        payroll_reader: PayrollReader,
        ingestor: PayrollIngestor,
        audit: AuditService,
        clock: Clock = utc_now,
    ) -> None:
        self._plans, self._planadmin = plans, planadmin
        self._payroll_reader, self._ingestor = payroll_reader, ingestor
        self._audit, self._clock = audit, clock

    def ingest(
        self, ctx: RequestContext, *, plan_id: str, pay_period_id: str, filename: str, data: bytes
    ) -> IngestResult:
        ctx.require(Scope.PAYROLL_INGEST)
        plan = self._plans.get_plan(ctx, plan_id)  # NotFound if not visible
        if self._payroll_reader.get_pay_period(pay_period_id) is None:
            raise NotFound("pay_period", pay_period_id)
        filename_hash = hashlib.sha256(filename.encode()).hexdigest()

        result = validate_payroll_csv(data, expected_pay_period_id=pay_period_id)
        if isinstance(result, UploadAccepted):
            # Every participant must exist in THIS plan's tenant; otherwise reject the whole file.
            tenant_scope = TenantScope(frozenset({plan.tenant_id}))
            for line, row in enumerate(result.rows, start=2):
                p = self._planadmin.get_participant(tenant_scope, row.participant_ref)
                if p is None or p.tenant_id != plan.tenant_id:
                    result = UploadRejected(
                        result.sha256,
                        result.row_count,
                        RejectionReason.INVALID_ROW,
                        f"line {line}, column 1: unknown participant",
                    )
                    break

        if isinstance(result, UploadRejected):
            self._audit.record(
                ctx,
                "payroll.upload_rejected",
                target_ref=plan.plan_id,
                tenant_id=plan.tenant_id,
                details={
                    "filename_hash": filename_hash,
                    "file_sha256": result.sha256,
                    "reason": result.reason.value,
                    "row_count": result.row_count,
                    "pay_period_id": pay_period_id,
                },
            )
            return IngestResult(False, None, result.row_count, result.reason.value, result.detail)

        file_id = self._ingestor.ingest_file(
            TenantScope(frozenset({plan.tenant_id})),
            tenant_id=plan.tenant_id,
            plan_id=plan.plan_id,
            pay_period_id=pay_period_id,
            file_sha256=result.sha256,
            rows=result.rows,
            received_at=self._clock(),
        )
        self._audit.record(
            ctx,
            "payroll.upload_accepted",
            target_ref=file_id,
            tenant_id=plan.tenant_id,
            details={
                "filename_hash": filename_hash,
                "file_sha256": result.sha256,
                "row_count": result.row_count,
                "pay_period_id": pay_period_id,
            },
        )
        return IngestResult(True, file_id, result.row_count, None, None)
