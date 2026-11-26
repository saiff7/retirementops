"""Audit records hold pseudonyms and route templates, never raw participant/employee identifiers."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

pytestmark = pytest.mark.integration
Bearer = Callable[[str], dict[str, str]]

RAW = r"PART-[0-9]{4}|(^|[^A-Za-z0-9])E-[0-9]{5}|[0-9]{3}-[0-9]{2}-[0-9]{4}"


def _latest(owner_engine: Engine, event: str) -> dict[str, object]:
    with owner_engine.connect() as conn:
        row = (
            conn.execute(
                text(
                    "SELECT target_ref, correlation_id, details FROM app.audit_events "
                    "WHERE event_type = :e ORDER BY seq DESC LIMIT 1"
                ),
                {"e": event},
            )
            .mappings()
            .one()
        )
    return dict(row)


def test_participant_ref_in_denied_path_is_pseudonymised(
    client: TestClient, bearer: Bearer, owner_engine: Engine
) -> None:
    r = client.get("/plans/PLAN-005/participants/PART-0001/enrollment", headers=bearer("op.casey"))
    assert r.status_code == 404
    row = _latest(owner_engine, "access.not_found")
    assert str(row["target_ref"]).startswith("pref_")
    details = row["details"]
    assert isinstance(details, dict)
    assert details["route"] == "/plans/{plan_id}/participants/{participant_ref}/enrollment"
    assert "PART-0001" not in str(row)


def test_same_ref_gives_same_pseudonym(
    client: TestClient, bearer: Bearer, owner_engine: Engine
) -> None:
    url = "/plans/PLAN-005/participants/PART-0002/enrollment"
    client.get(url, headers=bearer("op.casey"))
    first = _latest(owner_engine, "access.not_found")["target_ref"]
    client.get(url, headers=bearer("op.casey"))
    assert _latest(owner_engine, "access.not_found")["target_ref"] == first


@pytest.mark.parametrize(
    "supplied", ["PART-0001", "not-a-uuid", "E-40404-aaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]
)
def test_untrusted_correlation_ids_are_replaced(client: TestClient, supplied: str) -> None:
    r = client.get("/tickets", headers={"X-Correlation-ID": supplied})
    cid = r.headers["X-Correlation-ID"]
    assert cid != supplied and str(uuid.UUID(cid)) == cid


def test_valid_uuid_correlation_id_is_kept(client: TestClient) -> None:
    cid = str(uuid.uuid4())
    assert (
        client.get("/health", headers={"X-Correlation-ID": cid}).headers["X-Correlation-ID"] == cid
    )


def test_unmatched_routes_are_not_recorded_raw(
    client: TestClient, bearer: Bearer, owner_engine: Engine
) -> None:
    client.get("/plans/PLAN-001/participants/PART-0003/enrollment", headers=bearer("comp.jo"))
    details = _latest(owner_engine, "access.denied")["details"]
    assert isinstance(details, dict) and "{participant_ref}" in str(details["route"])


def test_no_raw_identifier_anywhere_in_audit_table(owner_engine: Engine) -> None:
    """Whole-table invariant over everything the suite has written so far."""
    with owner_engine.connect() as conn:
        n = conn.execute(
            text(
                "SELECT count(*) FROM app.audit_events WHERE "
                "(actor_principal || ' ' || coalesce(target_ref, '') || ' ' || "
                " coalesce(correlation_id, '') || ' ' || details::text) ~ :raw"
            ),
            {"raw": RAW},
        ).scalar_one()
        total = conn.execute(text("SELECT count(*) FROM app.audit_events")).scalar_one()
    assert total > 0 and n == 0
