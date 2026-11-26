"""API authentication, scope enforcement, cross-tenant denial, write-surface and log hygiene."""

from __future__ import annotations

import io
from collections.abc import Callable

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from retirementops.api.app import create_app
from retirementops.seed.generator import Dataset
from retirementops.services.container import Container

pytestmark = pytest.mark.integration
Bearer = Callable[[str], dict[str, str]]

ALDER_TICKET = "ZD-1001"  # first generated ticket belongs to the case for PART-0001 (Alder)
ALDER_URLS = [
    f"/tickets/{ALDER_TICKET}",
    "/plans/PLAN-001",
    "/plans/PLAN-001/rules/applicable?effective_date=2026-02-01&contribution_type=EMPLOYER_MATCH",
    "/plans/PLAN-001/participants/PART-0001/enrollment",
    "/plans/PLAN-001/participants/PART-0001/contributions?pay_period=PP-2026-02",
    "/plans/PLAN-001/participants/PART-0001/contribution-finding?pay_period=PP-2026-02",
]


def _audit_count(owner_engine: Engine, event: str) -> int:
    with owner_engine.connect() as conn:
        return int(
            conn.execute(
                text("SELECT count(*) FROM app.audit_events WHERE event_type = :e"), {"e": event}
            ).scalar_one()
        )


# ---------------------------------------------------------------- authentication
@pytest.mark.parametrize(
    "header",
    [
        None,
        "Bearer",
        "Bearer not-a-token",
        "Basic dXNlcjpwYXNz",
        "Bearer eyJhbGciOiJub25lIn0.eyJzdWIiOiJyZXYuc2FtIn0.",
    ],
)
def test_unauthenticated_requests_get_401(client: TestClient, header: str | None) -> None:
    r = client.get("/tickets", headers={"Authorization": header} if header else {})
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert set(r.json()) == {"error", "message", "correlation_id"}


def test_deactivated_user_is_rejected_immediately(
    client: TestClient, bearer: Bearer, owner_engine: Engine
) -> None:
    headers = bearer("op.alex")
    assert client.get("/tickets", headers=headers).status_code == 200
    with owner_engine.begin() as conn:
        conn.execute(text("UPDATE app.users SET active = false WHERE user_id = 'op.alex'"))
    try:
        assert client.get("/tickets", headers=headers).status_code == 401  # same, unexpired token
    finally:
        with owner_engine.begin() as conn:
            conn.execute(text("UPDATE app.users SET active = true WHERE user_id = 'op.alex'"))


def test_removed_tenant_assignment_takes_effect_immediately(
    client: TestClient, bearer: Bearer, owner_engine: Engine
) -> None:
    headers = bearer("op.alex")
    assert client.get("/plans/PLAN-003", headers=headers).status_code == 200  # Birchway
    with owner_engine.begin() as conn:
        conn.execute(
            text("UPDATE app.users SET tenant_ids = ARRAY['TEN-ALDER'] WHERE user_id = 'op.alex'")
        )
    try:
        assert client.get("/plans/PLAN-003", headers=headers).status_code == 404
    finally:
        with owner_engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE app.users SET tenant_ids = ARRAY['TEN-ALDER','TEN-BIRCH'] "
                    "WHERE user_id = 'op.alex'"
                )
            )


# ---------------------------------------------------------------- scopes
@pytest.mark.parametrize("url", ALDER_URLS)
def test_missing_scope_returns_403_and_is_audited(
    client: TestClient, bearer: Bearer, owner_engine: Engine, url: str
) -> None:
    before = _audit_count(owner_engine, "access.denied")
    r = client.get(url, headers=bearer("comp.jo"))  # compliance reviewer: no tickets/plans/payroll
    assert r.status_code == 403 and r.json()["error"] == "forbidden"
    assert _audit_count(owner_engine, "access.denied") == before + 1


def test_admin_has_no_business_data_access(client: TestClient, bearer: Bearer) -> None:
    assert client.get("/tickets", headers=bearer("admin.kim")).status_code == 403


def test_agent_service_cannot_get_a_dev_token(client: TestClient) -> None:
    assert client.post("/auth/dev-token", json={"user_id": "agent.worker"}).status_code == 404


# ---------------------------------------------------------------- tenant isolation
@pytest.mark.parametrize("url", ALDER_URLS)
def test_cross_tenant_read_denied(
    client: TestClient, bearer: Bearer, owner_engine: Engine, url: str
) -> None:
    """op.casey is assigned only to Cobalt. Every Alder object must look nonexistent (404)."""
    assert client.get(url, headers=bearer("rev.sam")).status_code == 200  # object exists
    before = _audit_count(owner_engine, "access.not_found")
    r = client.get(url, headers=bearer("op.casey"))
    assert r.status_code == 404, r.text
    assert "PART-0001" not in r.text and "Alder" not in r.text
    assert _audit_count(owner_engine, "access.not_found") == before + 1


def test_cross_tenant_participant_via_own_plan_denied(client: TestClient, bearer: Bearer) -> None:
    # Casey's own plan (Cobalt PLAN-005) + an Alder participant: object-level check must refuse.
    for path in (
        "enrollment",
        "contributions?pay_period=PP-2026-02",
        "contribution-finding?pay_period=PP-2026-02",
    ):
        r = client.get(f"/plans/PLAN-005/participants/PART-0001/{path}", headers=bearer("op.casey"))
        assert r.status_code == 404


def test_ticket_search_only_returns_own_tenants(
    client: TestClient, bearer: Bearer, dataset: Dataset
) -> None:
    cobalt = {
        tk["ticket_id"]
        for tk in dataset.tables["zendesk_mock.tickets"]
        if tk["tenant_id"] == "TEN-COBALT"
    }
    r = client.get("/tickets?page_size=25", headers=bearer("op.casey"))
    assert r.status_code == 200
    ids = [tk["ticket_id"] for tk in r.json()["items"]]
    assert ids and set(ids) <= cobalt
    for tid in ids[:5]:
        assert client.get(f"/tickets/{tid}", headers=bearer("op.casey")).status_code == 200
        assert client.get(f"/tickets/{tid}", headers=bearer("op.alex")).status_code == 404


def test_page_size_is_capped(client: TestClient, bearer: Bearer) -> None:
    assert client.get("/tickets?page_size=26", headers=bearer("op.alex")).status_code == 422
    assert client.get("/tickets?page_size=0", headers=bearer("op.alex")).status_code == 422


@pytest.mark.parametrize(
    "url",
    [
        "/tickets/ZD-1001' OR 1=1--",
        "/plans/PLAN-001;DROP",
        "/plans/PLAN-001/participants/PART-1/enrollment",
    ],
)
def test_malformed_identifiers_rejected(client: TestClient, bearer: Bearer, url: str) -> None:
    assert client.get(url, headers=bearer("rev.sam")).status_code in (404, 422)


# ---------------------------------------------------------------- write surface
def test_api_has_no_write_routes(container: Container) -> None:
    app = create_app(container)
    writes = {
        (r.path, m)
        for r in app.routes
        if isinstance(r, APIRoute)
        for m in r.methods
        if m not in {"GET", "HEAD"}
    }
    assert writes == {("/auth/dev-token", "POST")}


@pytest.mark.parametrize("method", ["PATCH", "PUT", "POST", "DELETE"])
def test_crafted_close_request_rejected(
    client: TestClient, bearer: Bearer, owner_engine: Engine, method: str
) -> None:
    for path in (
        f"/tickets/{ALDER_TICKET}",
        f"/tickets/{ALDER_TICKET}/close",
        f"/tickets/{ALDER_TICKET}/status",
    ):
        r = client.request(
            method,
            path,
            headers=bearer("rev.sam"),
            json={"status": "solved", "ticket_id": ALDER_TICKET},
        )
        assert r.status_code in (404, 405)
    with owner_engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT status FROM zendesk_mock.tickets WHERE ticket_id = :t"),
                {"t": ALDER_TICKET},
            ).scalar_one()
            == "new"
        )


def test_dev_token_rejects_extra_fields(client: TestClient) -> None:
    r = client.post("/auth/dev-token", json={"user_id": "rev.sam", "roles": ["admin"]})
    assert r.status_code == 422


# ---------------------------------------------------------------- logging
def test_request_logs_contain_no_token_body_or_participant_ref(
    client: TestClient, bearer: Bearer, log_stream: io.StringIO
) -> None:
    headers = bearer("rev.sam")
    token = headers["Authorization"].split()[1]
    client.get(f"/tickets/{ALDER_TICKET}", headers=headers)
    client.get(
        "/plans/PLAN-001/participants/PART-0001/contribution-finding?pay_period=PP-2026-02",
        headers=headers,
    )
    out = log_stream.getvalue()
    assert "http_request" in out
    assert token not in out and token[:20] not in out
    assert "PART-0001" not in out
    assert "/plans/{plan_id}/participants/{participant_ref}/contribution-finding" in out
    body = client.get(f"/tickets/{ALDER_TICKET}", headers=headers).json()["body"]
    assert body[:40] not in log_stream.getvalue()
