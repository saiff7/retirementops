"""OllamaProvider: local model over Ollama's HTTP API with JSON-schema structured output (ADR-005).

Safety properties (none depends on the model):
- The endpoint is configuration, never model input, and must be loopback unless explicitly allowed
  (no arbitrary URL fetching). No credentials, paths or query strings in the base URL.
- Each call has a timeout, a response size cap, temperature 0 and a fixed seed.
- The answer is validated by the same strict parser as every provider; failures are typed errors
  that never contain model output.
- The model digest is read from the local Ollama registry and recorded on each run (T-19).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from retirementops.agent_runtime.providers.contracts import (
    ClassificationRequest,
    ClassificationResult,
    DraftRequest,
    DraftResponse,
    ProviderOutputError,
    ProviderTimeout,
    ProviderUnavailable,
    ToolDecision,
    ToolDecisionRequest,
    _Answer,
    parse_answer,
)
from retirementops.agent_runtime.providers.prompts import (
    classification_messages,
    draft_messages,
    tool_messages,
)

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
MAX_RESPONSE_BYTES = 256 * 1024
# transport(method, url, json_body_or_None, timeout_seconds) -> parsed JSON object
Transport = Callable[[str, str, dict[str, Any] | None, float], dict[str, Any]]


def _urllib_transport(
    method: str, url: str, body: dict[str, Any] | None, timeout: float
) -> dict[str, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(  # noqa: S310 - scheme and host are validated in __init__
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
    except TimeoutError as exc:
        raise ProviderTimeout("model call timed out") from exc
    except urllib.error.HTTPError as exc:
        raise ProviderUnavailable(f"model backend returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        if isinstance(getattr(exc, "reason", None), TimeoutError):
            raise ProviderTimeout("model call timed out") from exc
        raise ProviderUnavailable("model backend unreachable") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ProviderOutputError("model backend response too large")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderUnavailable("model backend returned non-JSON") from exc
    if not isinstance(parsed, dict):
        raise ProviderUnavailable("model backend returned an unexpected shape")
    return parsed


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Pydantic emits $defs/$ref; inline them so the schema is self-contained for the backend."""
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(defs[node["$ref"].rsplit("/", 1)[-1]])
            return {k: walk(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    out = walk(schema)
    if not isinstance(out, dict):  # pragma: no cover - a schema is always an object
        raise TypeError("schema must be an object")
    return out


class OllamaProvider:
    def __init__(
        self,
        *,
        model: str,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 60.0,
        allow_non_loopback: bool = False,
        transport: Transport | None = None,
    ) -> None:
        parts = urlsplit(base_url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("Ollama URL must be http(s)://host:port")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("Ollama URL must not contain credentials, a query or a fragment")
        if parts.path not in {"", "/"}:
            raise ValueError("Ollama URL must not contain a path")
        if parts.hostname not in LOOPBACK_HOSTS and not allow_non_loopback:
            raise ValueError("Ollama URL must be a loopback address")
        if not model or len(model) > 128 or not 0 < timeout_seconds <= 600:
            raise ValueError("invalid model name or timeout")
        self._base = f"{parts.scheme}://{parts.netloc}"
        self._model, self._timeout = model, float(timeout_seconds)
        self._transport = transport or _urllib_transport
        self._digest: str | None = None

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def model(self) -> str:
        return self._model

    def model_digest(self) -> str | None:
        if self._digest is None:
            tags = self._transport("GET", f"{self._base}/api/tags", None, self._timeout)
            for m in tags.get("models", []):
                if isinstance(m, dict) and m.get("name") == self._model:
                    digest = m.get("digest")
                    self._digest = str(digest)[:128] if digest else None
        return self._digest

    def _chat[A: _Answer](self, messages: list[dict[str, str]], model: type[A]) -> A:
        body = {
            "model": self._model,
            "messages": messages,
            "format": _inline_refs(model.model_json_schema()),
            "stream": False,
            "think": False,
            "options": {"temperature": 0, "seed": 42},
        }
        reply = self._transport("POST", f"{self._base}/api/chat", body, self._timeout)
        message = reply.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise ProviderOutputError(f"{model.__name__}: backend reply has no content")
        return parse_answer(content, model)

    def classify_ticket(self, request: ClassificationRequest) -> ClassificationResult:
        return self._chat(classification_messages(request), ClassificationResult)

    def choose_tools(self, request: ToolDecisionRequest) -> ToolDecision:
        return self._chat(tool_messages(request), ToolDecision)

    def draft_response(self, request: DraftRequest) -> DraftResponse:
        return self._chat(draft_messages(request), DraftResponse)
