"""Roles, scopes and the verified principal. Pure data; enforcement happens in services and the DB."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from retirementops.domain.taxonomy import Queue


class Role(StrEnum):
    OPERATOR = "operator"
    REVIEWER = "reviewer"
    COMPLIANCE_REVIEWER = "compliance_reviewer"
    ADMIN = "admin"
    AGENT_SERVICE = "agent_service"


class Scope(StrEnum):
    TICKETS_READ = "tickets:read"
    PLANS_READ = "plans:read"
    PAYROLL_READ = "payroll:read"
    PAYROLL_INGEST = "payroll:ingest"
    DRAFTS_WRITE = "drafts:write"
    REVIEW_APPROVE = "review:approve"
    COMPLIANCE_READ = "compliance:read"
    COMPLIANCE_DRAFT = "compliance:draft"
    COMPLIANCE_APPROVE = "compliance:approve"
    ADMIN_OPS = "admin:ops"


APPROVAL_SCOPES = frozenset({Scope.REVIEW_APPROVE, Scope.COMPLIANCE_APPROVE})

# Mirrors docs/architecture.md §2. A unit test keeps the two in sync.
ROLE_SCOPES: dict[Role, frozenset[Scope]] = {
    Role.OPERATOR: frozenset(
        {
            Scope.TICKETS_READ,
            Scope.PLANS_READ,
            Scope.PAYROLL_READ,
            Scope.PAYROLL_INGEST,
            Scope.DRAFTS_WRITE,
        }
    ),
    Role.REVIEWER: frozenset(
        {
            Scope.TICKETS_READ,
            Scope.PLANS_READ,
            Scope.PAYROLL_READ,
            Scope.DRAFTS_WRITE,
            Scope.REVIEW_APPROVE,
        }
    ),
    Role.COMPLIANCE_REVIEWER: frozenset(
        {Scope.COMPLIANCE_READ, Scope.COMPLIANCE_DRAFT, Scope.COMPLIANCE_APPROVE}
    ),
    Role.ADMIN: frozenset({Scope.ADMIN_OPS}),
    # The agent can never hold an approval scope. Enforced again in effective_scopes().
    Role.AGENT_SERVICE: frozenset(
        {
            Scope.TICKETS_READ,
            Scope.PLANS_READ,
            Scope.PAYROLL_READ,
            Scope.DRAFTS_WRITE,
            Scope.COMPLIANCE_READ,
            Scope.COMPLIANCE_DRAFT,
        }
    ),
}

_TENANT_ID = re.compile(r"^TEN-[A-Z0-9]{2,12}$")


def validate_tenant_id(tenant_id: str) -> str:
    if not isinstance(tenant_id, str) or not _TENANT_ID.match(tenant_id):
        raise ValueError("invalid tenant id")
    return tenant_id


def scopes_for_roles(roles: frozenset[Role]) -> frozenset[Scope]:
    out: set[Scope] = set()
    for role in roles:
        out |= ROLE_SCOPES[role]
    return frozenset(out)


def effective_scopes(
    roles: frozenset[Role], requested: frozenset[Scope], *, is_human: bool
) -> frozenset[Scope]:
    """Token scopes ∩ scopes of roles held *now*. Non-humans never get approval scopes."""
    granted = scopes_for_roles(roles) & requested
    if not is_human or Role.AGENT_SERVICE in roles:
        granted -= APPROVAL_SCOPES
    return granted


@dataclass(frozen=True, slots=True)
class TenantScope:
    """The set of tenants a request may touch. Empty means *nothing* (fail closed)."""

    tenant_ids: frozenset[str]

    def __post_init__(self) -> None:
        for t in self.tenant_ids:
            validate_tenant_id(t)

    def allows(self, tenant_id: str) -> bool:
        return tenant_id in self.tenant_ids

    def as_setting(self) -> str:
        """Value for the Postgres RLS setting `retirementops.tenant_ids`."""
        return ",".join(sorted(self.tenant_ids))


# Scopes an agent run may ever carry (service-ops runs). Never approval, ingest or compliance.
AGENT_RUN_SCOPES = frozenset(
    {Scope.TICKETS_READ, Scope.PLANS_READ, Scope.PAYROLL_READ, Scope.DRAFTS_WRITE}
)
_RUN_ID = re.compile(r"^RUN-[0-9A-F]{16}$")
_USER_ID = re.compile(r"^[a-z]+\.[a-z]+$")
_TICKET = re.compile(r"^ZD-\d{4,6}$")
_PARTICIPANT = re.compile(r"^PART-\d{4}$")
_PLAN = re.compile(r"^PLAN-\d{3}$")
_PERIOD = re.compile(r"^PP-\d{4}-\d{2}$")
MAX_BOUND_SUBJECTS = 5


@dataclass(frozen=True, slots=True)
class RunBinding:
    """What one agent run may touch (architecture.md §2, threat model T-03/T-04).

    The HOST derives every field from the ticket and trusted connector data before the run starts;
    the model never supplies them. Services refuse anything outside the binding as not found, and
    an assignment proposal may name only `permitted_queue` (the routing rules engine's result).
    """

    run_id: str
    operator_id: str  # the human who started the run; re-checked on every request
    tenant_id: str
    ticket_id: str
    participant_refs: frozenset[str]
    plan_ids: frozenset[str]
    pay_period_ids: frozenset[str]
    permitted_queue: Queue | None

    def __post_init__(self) -> None:
        validate_tenant_id(self.tenant_id)
        checks = (
            (_RUN_ID, (self.run_id,)),
            (_USER_ID, (self.operator_id,)),
            (_TICKET, (self.ticket_id,)),
            (_PARTICIPANT, self.participant_refs),
            (_PLAN, self.plan_ids),
            (_PERIOD, self.pay_period_ids),
        )
        for pattern, values in checks:
            for v in values:
                if not isinstance(v, str) or not pattern.match(v):
                    raise ValueError("invalid run binding")
        for group in (self.participant_refs, self.plan_ids, self.pay_period_ids):
            if not isinstance(group, frozenset) or len(group) > MAX_BOUND_SUBJECTS:
                raise ValueError("invalid run binding")
        if self.permitted_queue is not None and not isinstance(self.permitted_queue, Queue):
            raise ValueError("invalid run binding")

    def as_claims(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "operator": self.operator_id,
            "tenant_id": self.tenant_id,
            "ticket_id": self.ticket_id,
            "participants": sorted(self.participant_refs),
            "plans": sorted(self.plan_ids),
            "pay_periods": sorted(self.pay_period_ids),
            "queue": self.permitted_queue.value if self.permitted_queue else None,
        }

    @classmethod
    def from_claims(cls, claims: object) -> RunBinding:
        if not isinstance(claims, dict):
            raise ValueError("invalid run binding")
        try:
            queue = claims["queue"]
            lists = [claims[k] for k in ("participants", "plans", "pay_periods")]
            if not all(isinstance(x, list) for x in lists):
                raise ValueError("invalid run binding")
            return cls(
                run_id=claims["run_id"],
                operator_id=claims["operator"],
                tenant_id=claims["tenant_id"],
                ticket_id=claims["ticket_id"],
                participant_refs=frozenset(lists[0]),
                plan_ids=frozenset(lists[1]),
                pay_period_ids=frozenset(lists[2]),
                permitted_queue=Queue(queue) if queue is not None else None,
            )
        except (KeyError, TypeError) as exc:
            raise ValueError("invalid run binding") from exc


@dataclass(frozen=True, slots=True)
class Principal:
    principal_id: str
    is_human: bool
    roles: frozenset[Role]
    scopes: frozenset[Scope]
    tenants: TenantScope
    run: RunBinding | None = None  # set only for agent run tokens

    def has_scope(self, scope: Scope) -> bool:
        return scope in self.scopes

    @property
    def primary_role(self) -> str:
        return sorted(r.value for r in self.roles)[0] if self.roles else "none"
