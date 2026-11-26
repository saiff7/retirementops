"""Shared fixtures. DB fixtures are lazy: unit tests never touch Postgres.

Integration/security tests use a dedicated database `retirementops_test`, recreated per session from
the same bootstrap + migrations + seed(42) path a developer uses.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime

import psycopg
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine

from retirementops.api.app import create_app
from retirementops.config import DbRole, Settings, get_settings
from retirementops.db.bootstrap import bootstrap
from retirementops.db.engines import make_engine
from retirementops.db.migrate import upgrade_head
from retirementops.domain.authz import Principal, scopes_for_roles
from retirementops.domain.evidence import SourceRef
from retirementops.governance.logging import configure_logging
from retirementops.governance.tokens import TokenService
from retirementops.seed.generator import Dataset, generate
from retirementops.seed.loader import db_table_hash, load_dataset
from retirementops.services.container import Container, build_container
from retirementops.services.context import RequestContext
from retirementops.services.request_guard import GuardConfig

TEST_DB = "retirementops_test"
FIXED_NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
LOADED_DB_HASH: dict[str, str] = {}
# The shared session client must not be throttled by the suite's own traffic; abuse-protection tests
# build their own app with strict limits (tests/security/test_abuse_protection.py).
GENEROUS_GUARD = GuardConfig(
    client_requests_per_minute=1_000_000,
    client_burst=1_000_000,
    auth_failures_per_minute=100_000,
    principal_requests_per_minute=1_000_000,
    principal_burst=1_000_000,
    anon_audit_per_client=1_000_000,
    anon_audit_global=1_000_000,
)


class FakeClock:
    """Controllable monotonic clock for rate-limit tests."""

    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_source(record_id: str = "X-1", record_type: str = "test") -> SourceRef:
    return SourceRef("test", record_type, record_id, "1", FIXED_NOW, "0" * 64)


# ------------------------------------------------------------------ keys / tokens (no DB)
def _keypair() -> tuple[bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private, public


@pytest.fixture(scope="session")
def keypair() -> tuple[bytes, bytes]:
    return _keypair()


@pytest.fixture(scope="session")
def other_keypair() -> tuple[bytes, bytes]:
    return _keypair()


@pytest.fixture(scope="session")
def token_service(keypair: tuple[bytes, bytes]) -> TokenService:
    private, public = keypair
    return TokenService(
        public_key_pem=public,
        private_key_pem=private,
        issuer="retirementops-dev",
        audience="retirementops-api",
        ttl_seconds=900,
    )


# ------------------------------------------------------------------ database
@pytest.fixture(scope="session")
def settings() -> Settings:
    return get_settings()


@pytest.fixture(scope="session")
def test_db(settings: Settings) -> str:
    try:
        bootstrap(settings, TEST_DB, drop_existing=True)
    except psycopg.OperationalError as exc:  # pragma: no cover - environment problem
        pytest.fail(
            f"Postgres is not reachable ({type(exc).__name__}). Run: docker compose up -d db",
            pytrace=False,
        )
    upgrade_head(TEST_DB)
    return TEST_DB


@pytest.fixture(scope="session")
def owner_engine(settings: Settings, test_db: str) -> Iterator[Engine]:
    engine = create_engine(
        settings.dsn(DbRole.OWNER, test_db), connect_args={"options": "-c timezone=UTC"}
    )
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def dataset(owner_engine: Engine) -> Dataset:
    ds = generate(42)
    load_dataset(owner_engine, ds)
    # Snapshot immediately: later tests legitimately add rows (uploads, audit events).
    LOADED_DB_HASH["value"] = db_table_hash(owner_engine, ds)
    return ds


@pytest.fixture(scope="session")
def role_engine(
    settings: Settings, test_db: str, dataset: Dataset
) -> Iterator[Callable[[DbRole], Engine]]:
    cache: dict[DbRole, Engine] = {}

    def get(role: DbRole) -> Engine:
        if role not in cache:
            cache[role] = make_engine(settings, role, test_db)
        return cache[role]

    yield get
    for e in cache.values():
        e.dispose()


@pytest.fixture(scope="session")
def container(
    settings: Settings, test_db: str, dataset: Dataset, token_service: TokenService
) -> Iterator[Container]:
    c = build_container(
        settings, db_name=test_db, tokens=token_service, guard_config=GENEROUS_GUARD
    )
    yield c
    c.dispose()


@pytest.fixture(scope="session")
def client(container: Container) -> Iterator[TestClient]:
    with TestClient(create_app(container)) as c:
        yield c


@pytest.fixture(scope="session")
def bearer(token_service: TokenService, dataset: Dataset) -> Callable[[str], dict[str, str]]:
    """Authorization header for a seeded user, requesting all scopes of their roles."""
    from retirementops.domain.authz import Role

    roles = {u["user_id"]: u["roles"] for u in dataset.tables["app.users"]}

    def make(user_id: str) -> dict[str, str]:
        scopes = scopes_for_roles(frozenset(Role(r) for r in roles[user_id]))
        token = token_service.issue(user_id, frozenset(s.value for s in scopes))
        return {"Authorization": f"Bearer {token}"}

    return make


@pytest.fixture(scope="session")
def ctx_for(
    container: Container, bearer: Callable[[str], dict[str, str]]
) -> Callable[[str], RequestContext]:
    def make(user_id: str) -> RequestContext:
        token = bearer(user_id)["Authorization"].split()[1]
        principal: Principal = container.identity.authenticate(token)
        return RequestContext(principal, f"test-{user_id}")

    return make


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    configure_logging(b"test-pepper", stream=stream)
    yield stream
    configure_logging(b"test-pepper", stream=io.StringIO())


@pytest.fixture
def d() -> Callable[[str], date]:
    return date.fromisoformat
