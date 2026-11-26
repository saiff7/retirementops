"""Tokens, log redaction, canonical JSON, the authz matrix and connector interface shape (no DB)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import inspect
import io
import json
import re
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from retirementops.config import ROOT
from retirementops.connectors import interfaces, payroll_mock, planadmin_mock, zendesk_mock
from retirementops.domain.authz import (
    APPROVAL_SCOPES,
    ROLE_SCOPES,
    Role,
    Scope,
    TenantScope,
    effective_scopes,
)
from retirementops.domain.evidence import canonical_json
from retirementops.governance.logging import configure_logging, get_logger
from retirementops.governance.tokens import TokenError, TokenService


# ---------------------------------------------------------------- tokens
def _svc(keys: tuple[bytes, bytes], **kw: object) -> TokenService:
    private, public = keys
    args: dict[str, object] = dict(
        public_key_pem=public,
        private_key_pem=private,
        issuer="retirementops-dev",
        audience="retirementops-api",
        ttl_seconds=900,
    )
    args.update(kw)
    return TokenService(**args)  # type: ignore[arg-type]


def test_token_round_trip(token_service: TokenService) -> None:
    claims = token_service.verify(token_service.issue("op.alex", frozenset({"tickets:read"})))
    assert claims.sub == "op.alex" and claims.scopes == {"tickets:read"}


def test_expired_token_rejected(keypair: tuple[bytes, bytes]) -> None:
    past = datetime.now(UTC) - timedelta(hours=2)
    old = _svc(keypair, clock=lambda: past).issue("op.alex", frozenset())
    with pytest.raises(TokenError):
        _svc(keypair).verify(old)


@pytest.mark.parametrize("kw", [{"audience": "other-api"}, {"issuer": "someone-else"}])
def test_wrong_audience_or_issuer_rejected(
    keypair: tuple[bytes, bytes], kw: dict[str, str]
) -> None:
    token = _svc(keypair, **kw).issue("op.alex", frozenset())
    with pytest.raises(TokenError):
        _svc(keypair).verify(token)


def test_token_signed_by_other_key_rejected(
    keypair: tuple[bytes, bytes], other_keypair: tuple[bytes, bytes]
) -> None:
    forged = _svc(other_keypair).issue("admin.kim", frozenset({"admin:ops"}))
    with pytest.raises(TokenError):
        _svc(keypair).verify(forged)


def _b64(obj: dict[str, object]) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


def _claims() -> dict[str, object]:
    now = int(datetime.now(UTC).timestamp())
    return {
        "iss": "retirementops-dev",
        "aud": "retirementops-api",
        "sub": "rev.sam",
        "iat": now,
        "nbf": now,
        "exp": now + 600,
        "jti": "x",
        "typ": "access",
        "scope": "review:approve",
    }


def test_alg_none_rejected(token_service: TokenService) -> None:
    token = f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(_claims())}."
    with pytest.raises(TokenError):
        token_service.verify(token)


def test_hs256_key_confusion_rejected(
    keypair: tuple[bytes, bytes], token_service: TokenService
) -> None:
    # Classic attack: sign HS256 using the PUBLIC key bytes as the HMAC secret.
    signing_input = f"{_b64({'alg': 'HS256', 'typ': 'JWT'})}.{_b64(_claims())}"
    sig = hmac.new(keypair[1], signing_input.encode(), hashlib.sha256).digest()
    token = signing_input + "." + base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
    with pytest.raises(TokenError):
        token_service.verify(token)


def test_tampered_and_oversized_tokens_rejected(token_service: TokenService) -> None:
    token = token_service.issue("op.alex", frozenset({"tickets:read"}))
    head, _payload, sig = token.split(".")
    tampered = f"{head}.{_b64({**_claims(), 'sub': 'admin.kim'})}.{sig}"
    for bad in (tampered, "x" * 5000, "", "not.a.jwt"):
        with pytest.raises(TokenError):
            token_service.verify(bad)


def test_token_missing_required_claim_rejected(
    keypair: tuple[bytes, bytes], token_service: TokenService
) -> None:
    claims = _claims()
    del claims["jti"]
    with pytest.raises(TokenError):
        token_service.verify(jwt.encode(claims, keypair[0], algorithm="RS256"))


# ---------------------------------------------------------------- log redaction
def test_logs_do_not_contain_canary_token_or_pii(log_stream: io.StringIO) -> None:
    # Built at runtime so no JWT-shaped literal is committed (scripts/check_no_secrets.py).
    canary_jwt = ".".join(
        ["eyJhbGciOiJSUzI1NiJ9", "eyJzdWIiOiJjYW5hcnkifQ", "c2lnbmF0dXJlLWNhbmFyeQ"]
    )
    get_logger("t").info(
        "probe",
        authorization=f"Bearer {canary_jwt}",
        ticket_body="CANARY-BODY-7f3a",
        eligible_pay_cents=123456,
        note=f"saw {canary_jwt} for PART-0001 ssn 000-12-3456 mail x.y@alderridge.example.test",
        nested={"access_token": "CANARY-NESTED", "ok": "PART-0002"},
    )
    out = log_stream.getvalue()
    for secret in (
        canary_jwt,
        "CANARY-BODY-7f3a",
        "123456",
        "PART-0001",
        "PART-0002",
        "000-12-3456",
        "x.y@alderridge.example.test",
        "CANARY-NESTED",
    ):
        assert secret not in out, secret
    record = json.loads(out.strip().splitlines()[-1])
    assert record["authorization"] == "[REDACTED]"
    assert record["note"].count("pref_") == 1  # pseudonymised, still correlatable
    assert record["event"] == "probe"


def test_pseudonym_is_stable_and_keyed() -> None:
    def pseudonym(pepper: bytes) -> str:
        s = io.StringIO()
        configure_logging(pepper, stream=s)
        get_logger("t").info("p", ref="PART-0007")
        return str(json.loads(s.getvalue())["ref"])

    assert pseudonym(b"a") == pseudonym(b"a") != pseudonym(b"b")


# ---------------------------------------------------------------- canonical json
def test_canonical_json_rejects_float_and_naive_datetime() -> None:
    with pytest.raises(TypeError):
        canonical_json({"x": 1.5})
    with pytest.raises(ValueError, match="naive"):
        canonical_json({"t": datetime(2026, 1, 1)})  # noqa: DTZ001
    assert canonical_json({"b": 1, "a": [True, None]}) == '{"a":[true,null],"b":1}'


# ---------------------------------------------------------------- authz matrix
def test_agent_and_admin_never_hold_approval_scopes() -> None:
    assert not ROLE_SCOPES[Role.AGENT_SERVICE] & APPROVAL_SCOPES
    assert not ROLE_SCOPES[Role.ADMIN] & APPROVAL_SCOPES
    every = frozenset(Scope)
    assert not effective_scopes(frozenset({Role.REVIEWER}), every, is_human=False) & APPROVAL_SCOPES
    assert (
        not effective_scopes(frozenset({Role.REVIEWER, Role.AGENT_SERVICE}), every, is_human=True)
        & APPROVAL_SCOPES
    )


def test_effective_scopes_are_an_intersection() -> None:
    got = effective_scopes(
        frozenset({Role.OPERATOR}),
        frozenset({Scope.TICKETS_READ, Scope.REVIEW_APPROVE}),
        is_human=True,
    )
    assert got == {Scope.TICKETS_READ}  # requesting approve does not grant it


def test_empty_tenant_scope_allows_nothing() -> None:
    assert TenantScope(frozenset()).as_setting() == ""
    with pytest.raises(ValueError):
        TenantScope(frozenset({"TEN-ALDER,TEN-BIRCH"}))  # cannot smuggle a list via one id


def test_scope_matrix_matches_architecture_doc() -> None:
    text = (ROOT / "docs" / "architecture.md").read_text()
    header = re.search(r"^\| Scope \|(.+)\|$", text, re.M)
    assert header
    roles = [Role(c.strip()) for c in header.group(1).split("|")]
    from_doc: dict[Role, set[Scope]] = {r: set() for r in roles}
    for m in re.finditer(r"^\| `([a-z]+:[a-z]+)` \|(.+)\|$", text, re.M):
        for role, cell in zip(roles, m.group(2).split("|"), strict=True):
            if "✓" in cell:
                from_doc[role].add(Scope(m.group(1)))
    assert {r: set(s) for r, s in ROLE_SCOPES.items()} == from_doc


# ---------------------------------------------------------------- connector shape
def _public_methods(cls: type) -> list[str]:
    return [n for n, _ in inspect.getmembers(cls, inspect.isfunction) if not n.startswith("_")]


ALL_CONNECTOR_CLASSES = [
    interfaces.TicketReader,
    interfaces.TicketWriter,
    interfaces.PlanAdminReader,
    interfaces.PayrollReader,
    interfaces.PayrollIngestor,
    zendesk_mock.ZendeskMockReader,
    zendesk_mock.ZendeskMockWriter,
    planadmin_mock.PlanAdminMockReader,
    payroll_mock.PayrollMockReader,
    payroll_mock.PayrollMockIngestor,
]


@pytest.mark.parametrize("cls", ALL_CONNECTOR_CLASSES, ids=lambda c: c.__name__)
def test_connector_has_no_status_mutation(cls: type) -> None:
    bad = [m for m in _public_methods(cls) if interfaces.FORBIDDEN_METHOD_PATTERN.search(m)]
    assert bad == [], f"{cls.__name__} exposes forbidden methods {bad}"


def test_ticket_writer_has_exactly_two_narrow_writes() -> None:
    assert sorted(_public_methods(interfaces.TicketWriter)) == [
        "apply_assignment",
        "post_public_reply",
    ]
    assert sorted(_public_methods(zendesk_mock.ZendeskMockWriter)) == [
        "apply_assignment",
        "post_public_reply",
    ]
