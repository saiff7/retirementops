"""Agent run tokens (ADR-008, architecture.md §2): minted only for an operator, bound, short-lived."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from retirementops.domain.authz import (
    AGENT_RUN_SCOPES,
    Principal,
    Role,
    RunBinding,
    Scope,
    TenantScope,
)
from retirementops.domain.taxonomy import Queue
from retirementops.governance.identity import UserRecord
from retirementops.governance.tokens import TokenService
from retirementops.services.errors import Forbidden, Unauthenticated
from retirementops.services.identity import AGENT_USER_ID, IdentityService

USERS = {
    "op.alex": UserRecord("op.alex", "human", ("operator",), ("TEN-ALDER", "TEN-BIRCH"), True),
    "rev.sam": UserRecord("rev.sam", "human", ("reviewer",), ("TEN-ALDER",), True),
    AGENT_USER_ID: UserRecord(AGENT_USER_ID, "service", ("agent_service",), (), True),
}
BINDING = RunBinding(
    run_id="RUN-0123456789ABCDEF",
    operator_id="op.alex",
    tenant_id="TEN-ALDER",
    ticket_id="ZD-1001",
    participant_refs=frozenset({"PART-0002"}),
    plan_ids=frozenset({"PLAN-001"}),
    pay_period_ids=frozenset({"PP-2026-03"}),
    permitted_queue=Queue.PAYROLL_OPERATIONS,
)


class FakeDirectory:
    def __init__(self, users: dict[str, UserRecord]) -> None:
        self.users = dict(users)

    def get(self, user_id: str) -> UserRecord | None:
        return self.users.get(user_id)


class Clock:
    """Issuing clock. Verification checks expiry against real time (PyJWT), so tests move this
    clock back to make a token old."""

    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def tokens(keypair: tuple[bytes, bytes], clock: Clock) -> TokenService:
    private, public = keypair
    return TokenService(
        public_key_pem=public,
        private_key_pem=private,
        issuer="retirementops-dev",
        audience="retirementops-api",
        ttl_seconds=900,
        clock=clock,
    )


@pytest.fixture
def directory() -> FakeDirectory:
    return FakeDirectory(USERS)


@pytest.fixture
def identity(tokens: TokenService, directory: FakeDirectory) -> IdentityService:
    return IdentityService(tokens, directory, dev_mode=False)  # type: ignore[arg-type]


def _operator(identity: IdentityService, tokens: TokenService, user: str = "op.alex") -> Principal:
    return identity.authenticate(tokens.issue(user, frozenset(s.value for s in AGENT_RUN_SCOPES)))


def _decode(token: str, keypair: tuple[bytes, bytes]) -> dict[str, object]:
    return jwt.decode(token, keypair[1], algorithms=["RS256"], audience="retirementops-api")


# ---------------------------------------------------------------- minting
def test_operator_mints_a_bound_minimal_run_token(
    identity: IdentityService, tokens: TokenService
) -> None:
    token = identity.issue_agent_run_token(_operator(identity, tokens), BINDING, AGENT_RUN_SCOPES)
    p = identity.authenticate(token)
    assert (p.principal_id, p.is_human, p.run) == (AGENT_USER_ID, False, BINDING)
    assert p.tenants == TenantScope(frozenset({"TEN-ALDER"}))
    assert p.scopes == AGENT_RUN_SCOPES
    assert not p.scopes & {Scope.REVIEW_APPROVE, Scope.COMPLIANCE_APPROVE, Scope.PAYROLL_INGEST}


def test_run_token_expires_after_ten_minutes(
    identity: IdentityService, tokens: TokenService, clock: Clock, keypair: tuple[bytes, bytes]
) -> None:
    operator = _operator(identity, tokens)
    token = identity.issue_agent_run_token(operator, BINDING, AGENT_RUN_SCOPES)
    claims = _decode(token, keypair)
    assert int(claims["exp"]) - int(claims["iat"]) == 600  # type: ignore[call-overload]
    assert claims["typ"] == "agent_run"
    clock.now = datetime.now(UTC) - timedelta(minutes=10, seconds=6)  # beyond the 5 s leeway
    old = identity.issue_agent_run_token(operator, BINDING, AGENT_RUN_SCOPES)
    with pytest.raises(Unauthenticated):
        identity.authenticate(old)


def test_reviewer_cannot_start_a_run(identity: IdentityService, tokens: TokenService) -> None:
    reviewer = _operator(identity, tokens, "rev.sam")
    with pytest.raises(Forbidden):
        identity.issue_agent_run_token(
            reviewer, replace(BINDING, operator_id="rev.sam"), AGENT_RUN_SCOPES
        )


@pytest.mark.parametrize(
    "binding",
    [
        replace(BINDING, tenant_id="TEN-COBALT"),  # operator not assigned to this tenant
        replace(BINDING, operator_id="rev.sam"),  # binding names someone else
    ],
)
def test_operator_cannot_mint_outside_their_own_authority(
    identity: IdentityService, tokens: TokenService, binding: RunBinding
) -> None:
    with pytest.raises(Forbidden):
        identity.issue_agent_run_token(_operator(identity, tokens), binding, AGENT_RUN_SCOPES)


@pytest.mark.parametrize(
    "scopes",
    [
        AGENT_RUN_SCOPES | {Scope.REVIEW_APPROVE},
        frozenset({Scope.COMPLIANCE_READ}),
        frozenset({Scope.PAYROLL_INGEST}),
        frozenset(),
    ],
)
def test_run_scopes_are_capped(
    identity: IdentityService, tokens: TokenService, scopes: frozenset[Scope]
) -> None:
    with pytest.raises(Forbidden):
        identity.issue_agent_run_token(_operator(identity, tokens), BINDING, scopes)


def test_an_agent_run_cannot_mint_another_run(
    identity: IdentityService, tokens: TokenService
) -> None:
    agent = identity.authenticate(
        identity.issue_agent_run_token(_operator(identity, tokens), BINDING, AGENT_RUN_SCOPES)
    )
    with pytest.raises(Forbidden):
        identity.issue_agent_run_token(agent, BINDING, AGENT_RUN_SCOPES)


# ---------------------------------------------------------------- authentication
@pytest.mark.parametrize(
    "change",
    [
        lambda u: replace(u, active=False),
        lambda u: replace(u, roles=("reviewer",)),
        lambda u: replace(u, tenant_ids=("TEN-BIRCH",)),
    ],
    ids=["deactivated", "lost-operator-role", "lost-tenant"],
)
def test_run_token_dies_when_the_starting_operator_loses_authority(
    identity: IdentityService, tokens: TokenService, directory: FakeDirectory, change: object
) -> None:
    token = identity.issue_agent_run_token(_operator(identity, tokens), BINDING, AGENT_RUN_SCOPES)
    directory.users["op.alex"] = change(USERS["op.alex"])  # type: ignore[operator]
    with pytest.raises(Unauthenticated):
        identity.authenticate(token)


def test_run_token_dies_when_the_agent_is_deactivated(
    identity: IdentityService, tokens: TokenService, directory: FakeDirectory
) -> None:
    token = identity.issue_agent_run_token(_operator(identity, tokens), BINDING, AGENT_RUN_SCOPES)
    directory.users[AGENT_USER_ID] = replace(USERS[AGENT_USER_ID], active=False)
    with pytest.raises(Unauthenticated):
        identity.authenticate(token)


def test_run_claims_on_a_human_subject_are_rejected(
    identity: IdentityService, tokens: TokenService
) -> None:
    forged = tokens.issue_agent_run("op.alex", frozenset({"tickets:read"}), BINDING.as_claims())
    with pytest.raises(Unauthenticated):
        identity.authenticate(forged)


def test_access_token_with_a_smuggled_run_claim_is_rejected(
    identity: IdentityService, keypair: tuple[bytes, bytes]
) -> None:
    now = int(datetime.now(UTC).timestamp())
    claims = {
        "iss": "retirementops-dev",
        "aud": "retirementops-api",
        "sub": AGENT_USER_ID,
        "iat": now,
        "nbf": now,
        "exp": now + 60,
        "jti": "x",
        "typ": "access",
        "scope": "tickets:read",
        "run": BINDING.as_claims(),
    }
    with pytest.raises(Unauthenticated):
        identity.authenticate(jwt.encode(claims, keypair[0], algorithm="RS256"))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: {**c, "tenant_id": "not-a-tenant"},
        lambda c: {**c, "participants": "PART-0002"},
        lambda c: {**c, "queue": "NOWHERE"},
        lambda c: {k: v for k, v in c.items() if k != "operator"},
        lambda c: {**c, "plans": [f"PLAN-{i:03d}" for i in range(6)]},
    ],
)
def test_malformed_bindings_are_rejected(
    identity: IdentityService, tokens: TokenService, mutate: object
) -> None:
    bad = tokens.issue_agent_run(
        AGENT_USER_ID,
        frozenset({"tickets:read"}),
        mutate(BINDING.as_claims()),  # type: ignore[operator]
    )
    with pytest.raises(Unauthenticated):
        identity.authenticate(bad)


def test_plain_agent_access_token_still_has_no_tenants(
    identity: IdentityService, tokens: TokenService
) -> None:
    p = identity.authenticate(tokens.issue(AGENT_USER_ID, frozenset({"tickets:read"})))
    assert p.run is None and p.tenants.tenant_ids == frozenset()
    assert Role.AGENT_SERVICE in p.roles


def test_binding_round_trips_through_claims() -> None:
    assert RunBinding.from_claims(BINDING.as_claims()) == BINDING
    assert RunBinding.from_claims({**BINDING.as_claims(), "queue": None}).permitted_queue is None


# ---------------------------------------------------------------- stage 6 review fixes
@pytest.mark.parametrize(("ttl", "expected"), [(120, 120), (600, 600), (10_000, 600)])
def test_ttl_can_only_shorten_a_run_token(
    tokens: TokenService, keypair: tuple[bytes, bytes], ttl: int, expected: int
) -> None:
    claims = _decode(
        tokens.issue_agent_run(
            AGENT_USER_ID, frozenset({"tickets:read"}), BINDING.as_claims(), ttl
        ),
        keypair,
    )
    assert int(claims["exp"]) - int(claims["iat"]) == expected  # type: ignore[call-overload]


@pytest.mark.parametrize("ttl", [0, -5, True, 1.5])
def test_invalid_ttl_is_refused(tokens: TokenService, ttl: object) -> None:
    from retirementops.governance.tokens import TokenError

    with pytest.raises(TokenError):
        tokens.issue_agent_run(AGENT_USER_ID, frozenset(), BINDING.as_claims(), ttl)  # type: ignore[arg-type]


def test_operator_check_accepts_an_assigned_operator(
    identity: IdentityService, tokens: TokenService
) -> None:
    op = _operator(identity, tokens)
    identity.require_run_operator(op)
    identity.require_run_operator(op, "TEN-ALDER")


@pytest.mark.parametrize(
    "change",
    [
        lambda u: replace(u, active=False),
        lambda u: replace(u, roles=("reviewer",)),
        lambda u: replace(u, tenant_ids=("TEN-BIRCH",)),
    ],
    ids=["deactivated", "lost-operator-role", "lost-tenant"],
)
def test_operator_check_rereads_the_directory(
    identity: IdentityService, tokens: TokenService, directory: FakeDirectory, change: object
) -> None:
    op = _operator(identity, tokens)  # authenticated while still authorised
    directory.users["op.alex"] = change(USERS["op.alex"])  # type: ignore[operator]
    with pytest.raises(Forbidden):
        identity.require_run_operator(op, "TEN-ALDER")


def test_operator_check_refuses_reviewers_agents_and_other_tenants(
    identity: IdentityService, tokens: TokenService
) -> None:
    with pytest.raises(Forbidden):
        identity.require_run_operator(_operator(identity, tokens, "rev.sam"))
    agent = identity.authenticate(
        identity.issue_agent_run_token(_operator(identity, tokens), BINDING, AGENT_RUN_SCOPES)
    )
    with pytest.raises(Forbidden):
        identity.require_run_operator(agent)
    with pytest.raises(Forbidden):
        identity.require_run_operator(_operator(identity, tokens), "TEN-COBALT")
