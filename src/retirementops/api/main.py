"""Process entry point (app factory; nothing happens at import time).

uv run uvicorn retirementops.api.main:create --factory --host 127.0.0.1 --port 8000
"""

from fastapi import FastAPI

from retirementops.api.app import create_app
from retirementops.config import Settings, get_settings
from retirementops.governance.logging import configure_logging
from retirementops.services.container import build_container


def create(settings: Settings | None = None, *, db_name: str | None = None) -> FastAPI:
    """Build the app from settings: real keys from disk, per-role engines, logging, guard.

    Engines are disposed when the app shuts down.
    """
    settings = settings or get_settings()
    container = build_container(settings, db_name=db_name)
    configure_logging(container.log_pepper)
    return create_app(container, dispose_on_shutdown=True)
