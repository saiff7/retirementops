"""The real process entry point: settings from .env, keys from keys/, logging, lifespan disposal."""

from __future__ import annotations

import importlib
import io
import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from retirementops.api import main
from retirementops.config import Settings
from retirementops.governance.logging import configure_logging
from retirementops.seed.generator import Dataset
from retirementops.services.container import Container

pytestmark = pytest.mark.integration


def test_import_has_no_side_effects() -> None:
    module = importlib.reload(main)
    assert not hasattr(module, "app")  # factory only: no engines or logging configured on import


def test_create_uses_real_settings_keys_and_logging(
    settings: Settings, test_db: str, dataset: Dataset, capsys: pytest.CaptureFixture[str]
) -> None:
    app = main.create(settings, db_name=test_db)
    try:
        with TestClient(app) as c:
            assert c.get("/health").json()["data"] == "synthetic"
            r = c.post("/auth/dev-token", json={"user_id": "rev.sam"})
            assert r.status_code == 200  # signed with keys/dev_jwt_private.pem via settings
            token = r.json()["access_token"]
            t = c.get("/tickets", headers={"Authorization": f"Bearer {token}"})
            assert t.status_code == 200 and t.json()["items"]
            uuid.UUID(t.headers["X-Correlation-ID"])
        err = capsys.readouterr().err
        records = [json.loads(line) for line in err.splitlines() if line.startswith("{")]
        assert any(
            rec.get("event") == "http_request" and rec.get("route") == "/tickets" for rec in records
        )
        assert token not in err
    finally:
        configure_logging(b"test-pepper", stream=io.StringIO())


def test_dev_token_unavailable_outside_dev(
    settings: Settings, test_db: str, dataset: Dataset
) -> None:
    app = main.create(
        settings.model_copy(update={"retirementops_env": "production"}), db_name=test_db
    )
    try:
        with TestClient(app) as c:
            assert c.get("/health").status_code == 200
            assert c.post("/auth/dev-token", json={"user_id": "rev.sam"}).status_code == 404
    finally:
        configure_logging(b"test-pepper", stream=io.StringIO())


def test_missing_private_key_cannot_issue_but_does_not_crash(
    settings: Settings, test_db: str, dataset: Dataset
) -> None:
    broken = settings.model_copy(update={"ro_jwt_private_key_path": Path("keys/absent.pem")})
    app = main.create(broken, db_name=test_db)
    try:
        with TestClient(app) as c:
            assert c.post("/auth/dev-token", json={"user_id": "rev.sam"}).status_code == 404
    finally:
        configure_logging(b"test-pepper", stream=io.StringIO())


def test_engines_are_disposed_on_shutdown(
    settings: Settings, test_db: str, dataset: Dataset, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    original = Container.dispose
    monkeypatch.setattr(Container, "dispose", lambda self: (calls.append(1), original(self)))
    app = main.create(settings, db_name=test_db)
    try:
        with TestClient(app) as c:
            c.get("/health")
            assert calls == []
        assert calls == [1]
    finally:
        configure_logging(b"test-pepper", stream=io.StringIO())
