"""Structured JSON logging with redaction (data-classification.md, threat T-11).

- Keys whose name contains a denied word (body, token, email, cents, ...) have their value removed.
- String values are scrubbed: JWTs, bearer tokens, SSN-like and email-like patterns are masked, and
  participant refs are replaced by an HMAC pseudonym so logs can be correlated without exposing refs.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any, TextIO

import structlog

DENIED_KEY_PARTS = frozenset(
    {
        "authorization",
        "bearer",
        "body",
        "cents",
        "cookie",
        "dsn",
        "email",
        "jwt",
        "password",
        "pw",
        "secret",
        "ssn",
        "subject",
        "text",
        "token",
    }
)
_JWT = re.compile(r"eyJ[\w-]{5,}\.[\w-]{5,}\.[\w-]{5,}")
_BEARER = re.compile(r"(?i)bearer\s+\S+")
_SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_PARTICIPANT = re.compile(r"PART-\d{4}")
# Review Low-5: also scrub passwords inside connection URLs and raw IPv4 addresses, which appear in
# third-party exception messages (database drivers, uvicorn) rather than in our own events.
_URL_PASSWORD = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^:/\s@]+:)[^@\s]+@")
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
REDACTED = "[REDACTED]"
# Third-party loggers whose own handlers are removed so they flow through the redacting root handler.
FOREIGN_LOGGERS = (
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
    "mcp",
    "httpx",
    "httpx2",
    "sqlalchemy",
    "alembic",
    "starlette",
)


def _key_denied(key: str) -> bool:
    return bool(DENIED_KEY_PARTS & set(re.split(r"[_\-.]", key.lower())))


def make_redactor(pepper: bytes) -> structlog.types.Processor:
    def pseudonym(match: re.Match[str]) -> str:
        return "pref_" + hmac.new(pepper, match.group(0).encode(), hashlib.sha256).hexdigest()[:12]

    def scrub(value: Any) -> Any:
        if isinstance(value, str):
            value = _URL_PASSWORD.sub(r"\1[REDACTED]@", value)
            value = _JWT.sub("[REDACTED_JWT]", value)
            value = _BEARER.sub("Bearer [REDACTED]", value)
            value = _SSN.sub("[REDACTED_SSN]", value)
            value = _EMAIL.sub("[REDACTED_EMAIL]", value)
            value = _IPV4.sub("[REDACTED_IP]", value)
            return _PARTICIPANT.sub(pseudonym, value)
        if isinstance(value, dict):
            return {k: REDACTED if _key_denied(str(k)) else scrub(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [scrub(v) for v in value]
        return value

    def processor(
        _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        for key in list(event_dict):
            event_dict[key] = REDACTED if _key_denied(key) else scrub(event_dict[key])
        return event_dict

    return processor


def configure_logging(
    pepper: bytes, stream: TextIO | None = None, level: int = logging.INFO
) -> None:
    """Configure structlog AND the standard-library root logger with the same redaction.

    Records from other libraries (the MCP SDK's `logger.exception`, uvicorn, SQLAlchemy) are
    rendered by a structlog ProcessorFormatter, so tracebacks are formatted to text and then
    scrubbed exactly like our own events. Nothing reaches the stream unredacted.
    """
    out = stream or sys.stderr
    redactor = make_redactor(pepper)
    handler = logging.StreamHandler(out)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=[
                structlog.stdlib.add_log_level,
                structlog.stdlib.add_logger_name,
                structlog.processors.TimeStamper(fmt="iso", utc=True),
                structlog.processors.format_exc_info,
            ],
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                redactor,
                structlog.processors.JSONRenderer(sort_keys=True),
            ],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for name in FOREIGN_LOGGERS:
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            redactor,  # last before rendering, so nothing bypasses it
            structlog.processors.JSONRenderer(sort_keys=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.WriteLoggerFactory(file=out),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> structlog.typing.FilteringBoundLogger:
    logger: structlog.typing.FilteringBoundLogger = structlog.get_logger(name)
    return logger
