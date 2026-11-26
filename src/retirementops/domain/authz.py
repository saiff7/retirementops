"""Roles, scopes and the verified principal. Pure data; enforcement happens in services and the DB."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


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


@dataclass(frozen=True, slots=True)
class Principal:
    principal_id: str
    is_human: bool
    roles: frozenset[Role]
    scopes: frozenset[Scope]
    tenants: TenantScope

    def has_scope(self, scope: Scope) -> bool:
        return scope in self.scopes

    @property
    def primary_role(self) -> str:
        return sorted(r.value for r in self.roles)[0] if self.roles else "none"
