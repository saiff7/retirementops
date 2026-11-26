"""Flooding cannot grow the audit log or reach authentication without bound (ADR-012)."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from retirementops.api.app import create_app
from retirementops.config import Settings
from retirementops.governance.tokens import TokenService
from retirementops.seed.generator import Dataset
from retirementops.services.container import build_container
from retirementops.services.request_guard import GuardConfig
from tests.conftest import FakeClock

pytestmark = pytest.mark.integration
Bearer = Callable[[str], dict[str, str]]
BAD = {"Authorization": "Bearer garbage.token.value"}

STRICT = GuardConfig(
    client_requests_per_minute=600,
    client_burst=1_000,  # high, so failed auth is what trips first
    auth_failures_per_minute=5,
    principal_requests_per_minute=5,
    principal_burst=5,
    audit_window_seconds=60,
    anon_audit_per_client=1,
    anon_audit_global=3,
)


@pytest.fixture
def strict_app(
    settings: Settings, test_db: str, dataset: Dataset, token_service: TokenService
) -> Iterator[tuple[FastAPI, FakeClock]]:
    clock = FakeClock()
    container = build_container(
        settings, db_name=test_db, tokens=token_service, guard_config=STRICT, monotonic=clock
    )
    yield create_app(container), clock
    container.dispose()


def _audit_rows(owner_engine: Engine) -> int:
    with owner_engine.connect() as conn:
        return int(conn.execute(text("SELECT count(*) FROM app.audit_events")).scalar_one())


def _client(app: FastAPI, ip: str) -> TestClient:
    return TestClient(app, client=(ip, 50000))


def test_single_source_flood_is_blocked_and_audited_once(
    strict_app: tuple[FastAPI, FakeClock], owner_engine: Engine, bearer: Bearer
) -> None:
    app, _ = strict_app
    before = _audit_rows(owner_engine)
    attacker = _client(app, "203.0.113.7")
    codes = [attacker.get("/tickets", headers=BAD).status_code for _ in range(200)]
    assert codes[:5] == [401] * 5  # failed-auth budget
    assert set(codes[5:]) == {429}  # then blocked before authentication
    blocked = attacker.get("/tickets", headers=BAD)
    assert int(blocked.headers["Retry-After"]) >= 1
    # A valid token does not unblock the flooding source (blocked before auth is attempted).
    assert attacker.get("/tickets", headers=bearer("rev.sam")).status_code == 429
    # 202 requests -> exactly one audit row for this source in this window.
    assert _audit_rows(owner_engine) - before == 1
    # Other clients are unaffected.
    assert (
        _client(app, "198.51.100.20").get("/tickets", headers=bearer("rev.sam")).status_code == 200
    )


def test_distributed_flood_is_globally_bounded_and_summarised(
    strict_app: tuple[FastAPI, FakeClock], owner_engine: Engine
) -> None:
    app, clock = strict_app
    before = _audit_rows(owner_engine)
    for i in range(50):
        c = _client(app, f"192.0.2.{i}")
        for _ in range(3):
            assert c.get("/tickets", headers=BAD).status_code == 401
    assert _audit_rows(owner_engine) - before == STRICT.anon_audit_global  # 150 events -> 3 rows

    clock.advance(61)  # next window: the first event carries a summary of what was suppressed
    _client(app, "192.0.2.250").get("/tickets", headers=BAD)
    with owner_engine.connect() as conn:
        summary = conn.execute(
            text(
                "SELECT details FROM app.audit_events WHERE event_type = "
                "'audit.anonymous_events_suppressed' ORDER BY seq DESC LIMIT 1"
            )
        ).scalar_one()
    assert summary == {"suppressed_events": 147, "distinct_sources": 50}
    assert _audit_rows(owner_engine) - before == STRICT.anon_audit_global + 2  # summary + new event


def test_block_lifts_after_refill(strict_app: tuple[FastAPI, FakeClock]) -> None:
    app, clock = strict_app
    c = _client(app, "203.0.113.50")
    for _ in range(6):
        c.get("/tickets", headers=BAD)
    assert c.get("/tickets", headers=BAD).status_code == 429
    clock.advance(60)
    assert c.get("/tickets", headers=BAD).status_code == 401


def test_authenticated_principal_budget_and_bounded_audit(
    strict_app: tuple[FastAPI, FakeClock], owner_engine: Engine, bearer: Bearer
) -> None:
    app, _ = strict_app
    before = _audit_rows(owner_engine)
    c = _client(app, "198.51.100.77")
    headers = bearer("op.alex")
    codes = [c.get("/tickets", headers=headers).status_code for _ in range(40)]
    assert codes[:5] == [200] * 5 and set(codes[5:]) == {429}
    # 35 rate-limited requests -> one rate_limit.exceeded row for this principal per window.
    assert _audit_rows(owner_engine) - before == 1
    with owner_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT actor_principal, event_type FROM app.audit_events ORDER BY seq DESC LIMIT 1"
            )
        ).one()
    assert tuple(row) == ("op.alex", "rate_limit.exceeded")


def test_audit_never_stores_raw_client_address(
    strict_app: tuple[FastAPI, FakeClock], owner_engine: Engine
) -> None:
    app, clock = strict_app
    clock.advance(1_000)  # fresh window
    _client(app, "203.0.113.99").get("/tickets", headers=BAD)
    with owner_engine.connect() as conn:
        details = conn.execute(
            text(
                "SELECT details FROM app.audit_events WHERE event_type = 'auth.failed' "
                "ORDER BY seq DESC LIMIT 1"
            )
        ).scalar_one()
        leaked = conn.execute(
            text("SELECT count(*) FROM app.audit_events WHERE details::text LIKE '%203.0.113.%'")
        ).scalar_one()
    assert details["client"].startswith("client_") and leaked == 0
