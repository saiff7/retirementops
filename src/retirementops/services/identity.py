"""Authenticate a bearer token into a verified Principal (ADR-008). Mock identity, not SSO.

Two token types:
- access tokens for seeded users: roles and tenants are re-read from the directory every request;
- agent run tokens (M3): minted only through `issue_agent_run_token` for a human operator who may
  see the ticket's tenant. The run's tenant comes from that binding, and on EVERY request the
  starting operator is re-read: if they were deactivated, lost the operator role or lost the tenant,
  the run token stops working at once. Run tokens never carry approval, ingest or compliance scopes.
"""

from __future__ import annotations

from retirementops.domain.authz import (
    AGENT_RUN_SCOPES,
    Principal,
    Role,
    RunBinding,
    Scope,
    TenantScope,
    effective_scopes,
    scopes_for_roles,
)
from retirementops.governance.identity import UserDirectory, UserRecord
from retirementops.governance.tokens import TokenClaims, TokenError, TokenService
from retirementops.services.errors import Forbidden, NotFound, Unauthenticated

_KNOWN_SCOPES = {s.value: s for s in Scope}
_KNOWN_ROLES = {r.value: r for r in Role}
AGENT_USER_ID = "agent.worker"


def _roles(user: UserRecord) -> frozenset[Role]:
    return frozenset(_KNOWN_ROLES[r] for r in user.roles if r in _KNOWN_ROLES)


class IdentityService:
    def __init__(self, tokens: TokenService, directory: UserDirectory, *, dev_mode: bool) -> None:
        self._tokens, self._directory, self._dev_mode = tokens, directory, dev_mode

    def authenticate(self, bearer: str | None) -> Principal:
        if not bearer:
            raise Unauthenticated("missing bearer token")
        try:
            claims = self._tokens.verify(bearer)
        except TokenError as exc:
            raise Unauthenticated("invalid token") from exc
        user = self._directory.get(claims.sub)
        if user is None or not user.active:
            raise Unauthenticated("unknown or inactive principal")
        roles = _roles(user)
        requested = frozenset(_KNOWN_SCOPES[s] for s in claims.scopes if s in _KNOWN_SCOPES)
        if claims.run is not None:
            return self._agent_run_principal(user, roles, requested, claims)
        is_human = user.kind == "human"
        return Principal(
            principal_id=user.user_id,
            is_human=is_human,
            roles=roles,
            scopes=effective_scopes(roles, requested, is_human=is_human),
            tenants=TenantScope(frozenset(user.tenant_ids)),  # always from the directory
        )

    def _agent_run_principal(
        self,
        user: UserRecord,
        roles: frozenset[Role],
        requested: frozenset[Scope],
        claims: TokenClaims,
    ) -> Principal:
        if user.kind != "service" or Role.AGENT_SERVICE not in roles:
            raise Unauthenticated("run tokens are only valid for the agent service")
        try:
            binding = RunBinding.from_claims(claims.run)
        except ValueError as exc:
            raise Unauthenticated("invalid token") from exc
        if not self._operator_may_start(binding.operator_id, binding.tenant_id):
            raise Unauthenticated("the operator who started this run is no longer authorised")
        scopes = effective_scopes(roles, requested, is_human=False) & AGENT_RUN_SCOPES
        return Principal(
            principal_id=user.user_id,
            is_human=False,
            roles=roles,
            scopes=scopes,
            tenants=TenantScope(frozenset({binding.tenant_id})),
            run=binding,
        )

    def _operator_may_start(self, operator_id: str, tenant_id: str) -> bool:
        op = self._directory.get(operator_id)
        return (
            op is not None
            and op.active
            and op.kind == "human"
            and Role.OPERATOR in _roles(op)
            and tenant_id in op.tenant_ids
        )

    def issue_agent_run_token(
        self, operator: Principal, binding: RunBinding, scopes: frozenset[Scope]
    ) -> str:
        """Mint a run token for `binding`. Called by the trusted orchestrator host, in process."""
        if (
            not operator.is_human
            or operator.run is not None
            or Role.OPERATOR not in operator.roles
            or binding.operator_id != operator.principal_id
            or not operator.tenants.allows(binding.tenant_id)
            or not self._operator_may_start(operator.principal_id, binding.tenant_id)
        ):
            raise Forbidden("only an operator assigned to the ticket's tenant can start a run")
        if not scopes or not scopes <= AGENT_RUN_SCOPES:
            raise Forbidden("requested run scopes exceed what an agent run may hold")
        agent = self._directory.get(AGENT_USER_ID)
        if agent is None or not agent.active or agent.kind != "service":
            raise Forbidden("the agent service principal is not available")
        try:
            return self._tokens.issue_agent_run(
                AGENT_USER_ID, frozenset(s.value for s in scopes), binding.as_claims()
            )
        except TokenError as exc:  # e.g. no private key configured in this process
            raise Forbidden("this process cannot mint run tokens") from exc

    @property
    def dev_mode(self) -> bool:
        return self._dev_mode

    def issue_dev_token(self, user_id: str) -> str:
        """DEV ONLY. Issues a token for a seeded human user with their role scopes."""
        if not self._dev_mode:
            raise NotFound("route", "dev-token")
        user = self._directory.get(user_id)
        if user is None or not user.active or user.kind != "human":
            raise NotFound("user", user_id)
        try:
            return self._tokens.issue(
                user.user_id, frozenset(s.value for s in scopes_for_roles(_roles(user)))
            )
        except TokenError as exc:  # e.g. no private key configured in this process
            raise NotFound("route", "dev-token") from exc
