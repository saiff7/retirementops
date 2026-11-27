"""Draft replies and assignment proposals. Records only: nothing is sent, assigned or closed here.

Draft status (ADR-013, M3): every draft, from any client, is checked by the server-side validator
(`DraftValidationService`) before it is inserted. Only a passing draft is stored as PENDING_REVIEW;
anything else is NEEDS_MANUAL_REVIEW with value-free reasons. The database guard (migration 0006)
independently refuses PENDING_REVIEW without a validator version and with outstanding reasons.
Either way a human approves before anything is sent (M5).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from retirementops.clock import Clock, utc_now
from retirementops.connectors.interfaces import TicketReader
from retirementops.domain.authz import Scope
from retirementops.domain.draft_validator import ValidationReport
from retirementops.domain.records import Ticket
from retirementops.domain.taxonomy import TERMINAL_TICKET_STATUSES, Queue
from retirementops.governance.pseudonym import contains_restricted
from retirementops.services.audit_service import AuditService
from retirementops.services.context import RequestContext
from retirementops.services.draft_validation import DraftValidationService
from retirementops.services.errors import InvalidRequest, NotFound
from retirementops.services.evidence import EvidenceRef, EvidenceVerifier
from retirementops.store.workflow import (
    DraftConflict,
    DraftRecord,
    ProposalRecord,
    WorkflowStore,
    draft_content_hash,
)


@dataclass(frozen=True, slots=True)
class DraftResult:
    draft: DraftRecord
    evidence_count: int
    validation: ValidationReport | None = None  # None only for an idempotent replay


class DraftingService:
    def __init__(
        self,
        *,
        tickets: TicketReader,
        evidence: EvidenceVerifier,
        validator: DraftValidationService,
        store: WorkflowStore,
        audit: AuditService,
        clock: Clock = utc_now,
    ) -> None:
        self._tickets, self._evidence, self._store = tickets, evidence, store
        self._validator, self._audit, self._clock = validator, audit, clock

    def _open_ticket(self, ctx: RequestContext, ticket_id: str) -> Ticket:
        ctx.bound_ticket(ticket_id)
        ticket = self._tickets.get_ticket(ctx.principal.tenants, ticket_id)
        if ticket is None:
            raise NotFound("ticket", ticket_id)
        if ticket.status in TERMINAL_TICKET_STATUSES:
            raise InvalidRequest("ticket is solved or closed; drafts and proposals are not allowed")
        return ticket

    def create_reply_draft(
        self,
        ctx: RequestContext,
        *,
        ticket_id: str,
        body: str,
        evidence_refs: Sequence[EvidenceRef],
        skill_version: str,
        idempotency_key: str | None = None,
    ) -> DraftResult:
        ctx.require(Scope.DRAFTS_WRITE, Scope.TICKETS_READ)
        ticket = self._open_ticket(ctx, ticket_id)
        if contains_restricted(body):
            raise InvalidRequest("draft body contains a restricted identifier pattern (POL-009)")
        # Review Low-7: a reply must cite the ticket it answers (baseline evidence ownership).
        if not any(
            (r.system, r.record_type, r.record_id) == ("zendesk_mock", "ticket", ticket.ticket_id)
            for r in evidence_refs
        ):
            raise InvalidRequest("evidence must include the ticket being answered")
        p = ctx.principal
        # N-1: the caller must be able to read every cited record type on EVERY path, before any
        # replay lookup. Same answer whether or not a matching draft exists.
        EvidenceVerifier.require_readable(evidence_refs, p.scopes)
        if idempotency_key is not None:
            # Review Low-8 / N-1: only the same user's own earlier request is replayed. Another
            # user's key is simply not found, so that request is treated as new (no existence
            # signal). A replay skips re-verification (evidence may have changed since) and does
            # not write a second audit event.
            existing = self._store.find_draft_by_idempotency_key(
                p.tenants,
                tenant_id=ticket.tenant_id,
                created_by=p.principal_id,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                wanted = draft_content_hash(
                    ticket_id=ticket.ticket_id,
                    body=body,
                    evidence_refs=[r.as_dict() for r in evidence_refs],
                )
                if (
                    existing.ticket_id != ticket.ticket_id
                    or existing.content_hash != wanted
                    or existing.skill_version != skill_version
                ):
                    raise InvalidRequest("idempotency key already used for a different draft")
                return DraftResult(existing, len(evidence_refs))
        verified = self._evidence.verify(
            evidence_refs,
            tenant_id=ticket.tenant_id,
            today=self._clock().date(),
            caller_scopes=p.scopes,
            binding=p.run,
        )
        report = self._validator.validate(ctx, ticket=ticket, body=body, cited=verified)
        try:
            draft = self._create(
                ctx, ticket, body, evidence_refs, report, skill_version, idempotency_key
            )
        except DraftConflict as exc:
            raise InvalidRequest("idempotency key already used; retry to fetch the draft") from exc
        self._audit.record(
            ctx,
            "draft.created",
            target_ref=draft.draft_id,
            tenant_id=ticket.tenant_id,
            details={
                "ticket_id": ticket.ticket_id,
                "version": draft.version,
                "draft_hash": draft.content_hash,
                "evidence_count": len(verified),
                "skill_version": skill_version,
                "status": draft.status,
                "validator_version": report.validator_version,
                "validator_issues": sorted({i.code.value for i in report.issues}),
            },
        )
        return DraftResult(draft, len(verified), report)

    def _create(
        self,
        ctx: RequestContext,
        ticket: Ticket,
        body: str,
        refs: Sequence[EvidenceRef],
        report: ValidationReport,
        skill_version: str,
        idempotency_key: str | None,
    ) -> DraftRecord:
        p = ctx.principal
        return self._store.create_draft(
            p.tenants,
            ticket_id=ticket.ticket_id,
            tenant_id=ticket.tenant_id,
            body=body,
            evidence_refs=[r.as_dict() for r in refs],
            skill_version=skill_version,
            ticket_version=ticket.version,
            author_principal=p.principal_id,
            author_kind="human" if p.is_human else "service",
            status="PENDING_REVIEW" if report.passed else "NEEDS_MANUAL_REVIEW",
            status_reasons=list(report.reasons),
            validator_version=report.validator_version,
            idempotency_key=idempotency_key,
        )

    def propose_ticket_assignment(
        self, ctx: RequestContext, *, ticket_id: str, target_queue: Queue, rationale: str
    ) -> ProposalRecord:
        ctx.require(Scope.DRAFTS_WRITE, Scope.TICKETS_READ)
        ticket = self._open_ticket(ctx, ticket_id)
        ctx.permitted_queue(target_queue)  # ADR-013 / Low-7b: the rules engine's queue only
        if contains_restricted(rationale):
            raise InvalidRequest("rationale contains a restricted identifier pattern (POL-009)")
        proposal = self._store.create_or_get_proposal(
            ctx.principal.tenants,
            ticket_id=ticket.ticket_id,
            tenant_id=ticket.tenant_id,
            target_queue=target_queue.value,
            rationale=rationale,
            ticket_version=ticket.version,
            created_by=ctx.principal.principal_id,
        )
        if not proposal.deduplicated:
            self._audit.record(
                ctx,
                "proposal.created",
                target_ref=proposal.proposal_id,
                tenant_id=ticket.tenant_id,
                details={
                    "ticket_id": ticket.ticket_id,
                    "action": proposal.action_type,
                    "target_queue": proposal.target_queue,
                    "status": proposal.status,
                },
            )
        return proposal

    def get_action_status(self, ctx: RequestContext, proposal_id: str) -> ProposalRecord:
        ctx.require(Scope.TICKETS_READ)
        proposal = self._store.get_proposal(ctx.principal.tenants, proposal_id)
        run = ctx.principal.run
        if proposal is None or (run is not None and proposal.ticket_id != run.ticket_id):
            raise NotFound("proposal", proposal_id)
        return proposal
