"""Authenticate a bearer token into a verified Principal (ADR-008). Mock identity, not SSO."""

from __future__ import annotations

from retirementops.domain.authz import (
    Principal,
    Role,
    Scope,
    TenantScope,
    effective_scopes,
    scopes_for_roles,
)
from retirementops.governance.identity import UserDirectory
from retirementops.governance.tokens import TokenError, TokenService
from retirementops.services.errors import NotFound, Unauthenticated

_KNOWN_SCOPES = {s.value: s for s in Scope}
_KNOWN_ROLES = {r.value: r for r in Role}


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
        roles = frozenset(_KNOWN_ROLES[r] for r in user.roles if r in _KNOWN_ROLES)
        requested = frozenset(_KNOWN_SCOPES[s] for s in claims.scopes if s in _KNOWN_SCOPES)
        is_human = user.kind == "human"
        return Principal(
            principal_id=user.user_id,
            is_human=is_human,
            roles=roles,
            scopes=effective_scopes(roles, requested, is_human=is_human),
            tenants=TenantScope(frozenset(user.tenant_ids)),  # always from the directory
        )

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
        roles = frozenset(_KNOWN_ROLES[r] for r in user.roles if r in _KNOWN_ROLES)
        try:
            return self._tokens.issue(
                user.user_id, frozenset(s.value for s in scopes_for_roles(roles))
            )
        except TokenError as exc:  # e.g. no private key configured in this process
            raise NotFound("route", "dev-token") from exc
