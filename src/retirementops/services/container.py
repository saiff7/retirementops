"""Wire connectors (each on its own DB role) into services. The only place engines meet services."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import Engine

from retirementops.clock import Clock, utc_now
from retirementops.config import DbRole, Settings
from retirementops.connectors.payroll_mock import PayrollMockIngestor, PayrollMockReader
from retirementops.connectors.planadmin_mock import PlanAdminMockReader
from retirementops.connectors.servicekb_mock import PolicyKbMockReader
from retirementops.connectors.zendesk_mock import ZendeskMockReader
from retirementops.db.engines import make_runtime_engines
from retirementops.governance.audit import AuditLog
from retirementops.governance.identity import UserDirectory
from retirementops.governance.pseudonym import Pseudonymiser
from retirementops.governance.tokens import TokenService
from retirementops.services.audit_service import AuditService
from retirementops.services.business import ContributionService, PlanService, TicketService
from retirementops.services.draft_validation import DraftValidationService
from retirementops.services.drafting import DraftingService
from retirementops.services.evidence import EvidenceVerifier
from retirementops.services.identity import IdentityService
from retirementops.services.payroll_ingest import PayrollIngestService
from retirementops.services.policy import PolicyService
from retirementops.services.request_guard import GuardConfig, RequestGuard
from retirementops.store.workflow import WorkflowStore


@dataclass
class Container:
    identity: IdentityService
    audit: AuditService
    tickets: TicketService
    plans: PlanService
    contributions: ContributionService
    payroll_ingest: PayrollIngestService
    guard: RequestGuard
    policies: PolicyService
    drafting: DraftingService
    log_pepper: bytes
    engines: dict[DbRole, Engine] = field(repr=False)

    def dispose(self) -> None:
        for engine in self.engines.values():
            engine.dispose()


def token_service_from_settings(settings: Settings) -> TokenService:
    private_path = settings.resolve(settings.ro_jwt_private_key_path)
    return TokenService(
        public_key_pem=settings.resolve(settings.ro_jwt_public_key_path).read_bytes(),
        private_key_pem=private_path.read_bytes() if private_path.exists() else None,
        issuer=settings.ro_jwt_issuer,
        audience=settings.ro_jwt_audience,
        ttl_seconds=settings.ro_jwt_ttl_seconds,
        run_ttl_seconds=settings.ro_agent_run_ttl_seconds,
    )


def guard_config_from_settings(settings: Settings) -> GuardConfig:
    return GuardConfig(
        client_requests_per_minute=settings.ro_rl_client_per_minute,
        client_burst=settings.ro_rl_client_burst,
        auth_failures_per_minute=settings.ro_rl_auth_failures_per_minute,
        principal_requests_per_minute=settings.ro_rl_principal_per_minute,
        principal_burst=settings.ro_rl_principal_burst,
        anon_audit_per_client=settings.ro_audit_anon_per_client_per_window,
        anon_audit_global=settings.ro_audit_anon_global_per_window,
    )


def build_container(
    settings: Settings,
    *,
    db_name: str | None = None,
    tokens: TokenService | None = None,
    clock: Clock = utc_now,
    guard_config: GuardConfig | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> Container:
    engines = make_runtime_engines(settings, db_name)
    planadmin = PlanAdminMockReader(engines[DbRole.PLANADMIN_READER], clock)
    payroll = PayrollMockReader(engines[DbRole.PAYROLL_READER], clock)
    audit = AuditService(
        AuditLog(engines[DbRole.APP_SERVICE], clock),
        Pseudonymiser(settings.ro_audit_pepper.get_secret_value().encode()),
    )
    plans = PlanService(planadmin)
    contributions = ContributionService(plans, planadmin, payroll)
    tickets = ZendeskMockReader(engines[DbRole.ZENDESK_READER], clock)
    kb = PolicyKbMockReader(engines[DbRole.SERVICEKB_READER], clock)
    drafting = DraftingService(
        tickets=tickets,
        evidence=EvidenceVerifier(
            tickets=tickets, planadmin=planadmin, payroll=payroll, policies=kb
        ),
        validator=DraftValidationService(contributions),
        store=WorkflowStore(engines[DbRole.APP_SERVICE], clock),
        audit=audit,
        clock=clock,
    )
    return Container(
        identity=IdentityService(
            tokens or token_service_from_settings(settings),
            UserDirectory(engines[DbRole.APP_SERVICE]),
            dev_mode=settings.retirementops_env == "dev",
        ),
        audit=audit,
        tickets=TicketService(tickets),
        policies=PolicyService(kb, clock),
        drafting=drafting,
        plans=plans,
        contributions=contributions,
        payroll_ingest=PayrollIngestService(
            plans=plans,
            planadmin=planadmin,
            payroll_reader=payroll,
            ingestor=PayrollMockIngestor(engines[DbRole.PAYROLL_INGEST]),
            audit=audit,
            clock=clock,
        ),
        guard=RequestGuard(guard_config or guard_config_from_settings(settings), audit, monotonic),
        log_pepper=settings.ro_log_pepper.get_secret_value().encode(),
        engines=engines,
    )
