"""Mock identity tokens: RS256 JWTs (ADR-008). NOT production SSO.

Verification is strict: one allowed algorithm, required claims, issuer and audience checked, and a
size cap. Tokens carry identity and *requested* scopes only. Roles and tenants are re-read from the
user directory on every request.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from retirementops.clock import Clock, utc_now

ALGORITHM = "RS256"
MAX_TOKEN_BYTES = 4096
_SUB = re.compile(r"^[a-z]+\.[a-z]+$")
_SCOPE = re.compile(r"^[a-z]+:[a-z]+$")


class TokenError(Exception):
    """Token missing, malformed, expired, or not signed by our key. Never carries the token."""


@dataclass(frozen=True, slots=True)
class TokenClaims:
    sub: str
    scopes: frozenset[str]
    jti: str
    expires_at: datetime


class TokenService:
    def __init__(
        self,
        *,
        public_key_pem: bytes,
        private_key_pem: bytes | None,
        issuer: str,
        audience: str,
        ttl_seconds: int,
        clock: Clock = utc_now,
    ) -> None:
        self._public, self._private = public_key_pem, private_key_pem
        self._issuer, self._audience = issuer, audience
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock

    def issue(self, sub: str, scopes: frozenset[str]) -> str:
        if self._private is None:
            raise TokenError("this process cannot issue tokens")
        if not _SUB.match(sub) or not all(_SCOPE.match(s) for s in scopes):
            raise TokenError("invalid subject or scope")
        now = self._clock()
        claims = {
            "iss": self._issuer,
            "aud": self._audience,
            "sub": sub,
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()),
            "exp": int((now + self._ttl).timestamp()),
            "jti": uuid.uuid4().hex,
            "typ": "access",
            "scope": " ".join(sorted(scopes)),
        }
        return jwt.encode(claims, self._private, algorithm=ALGORITHM)

    def verify(self, token: str) -> TokenClaims:
        if not token or len(token.encode()) > MAX_TOKEN_BYTES:
            raise TokenError("invalid token")
        try:
            claims = jwt.decode(
                token,
                self._public,
                algorithms=[ALGORITHM],
                audience=self._audience,
                issuer=self._issuer,
                leeway=5,
                options={"require": ["exp", "iat", "nbf", "iss", "aud", "sub", "jti"]},
            )
        except jwt.PyJWTError as exc:
            raise TokenError("invalid token") from exc
        sub, scope = claims.get("sub"), claims.get("scope", "")
        if claims.get("typ") != "access" or not isinstance(sub, str) or not _SUB.match(sub):
            raise TokenError("invalid token")
        if not isinstance(scope, str):
            raise TokenError("invalid token")
        scopes = frozenset(s for s in scope.split() if _SCOPE.match(s))
        return TokenClaims(
            sub, scopes, str(claims["jti"]), datetime.fromtimestamp(int(claims["exp"]), UTC)
        )
