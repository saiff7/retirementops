"""Canonical serialisation, content hashes and evidence source references."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum


def _normalise(obj: object) -> object:
    if obj is None or isinstance(obj, (bool, str, int)):
        return obj
    if isinstance(obj, float):
        raise TypeError("floats are not allowed in canonical data (use Decimal or int cents)")
    if isinstance(obj, Decimal):
        if not obj.is_finite():
            raise ValueError("non-finite Decimal")
        return str(obj)
    if isinstance(obj, Enum):
        return _normalise(obj.value)
    if isinstance(obj, datetime):
        if obj.tzinfo is None or obj.utcoffset() is None:
            raise ValueError("naive datetimes are not allowed; use timezone-aware UTC")
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _normalise(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, Mapping):
        out: dict[str, object] = {}
        for k, v in obj.items():
            if not isinstance(k, str):
                raise TypeError("mapping keys must be str")
            out[k] = _normalise(v)
        return out
    if isinstance(obj, Sequence) and not isinstance(obj, (bytes, bytearray)):
        return [_normalise(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted(_normalise(v) for v in obj)  # type: ignore[type-var]
    raise TypeError(f"cannot canonicalise {type(obj).__name__}")


def canonical_json(obj: object) -> str:
    """Deterministic JSON: sorted keys, no whitespace, no floats, tz-aware datetimes only."""
    return json.dumps(
        _normalise(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(obj: object) -> str:
    return sha256_hex(canonical_json(obj))


@dataclass(frozen=True, slots=True)
class SourceRef:
    """Provenance for one piece of evidence. Snapshots are copies, never authority."""

    system: str  # e.g. "planadmin_mock"
    record_type: str  # e.g. "plan_rule"
    record_id: str
    record_version: str
    retrieved_at: datetime
    content_hash: str

    def __post_init__(self) -> None:
        if self.retrieved_at.tzinfo is None:
            raise ValueError("retrieved_at must be timezone-aware")
        if len(self.content_hash) != 64:
            raise ValueError("content_hash must be a sha256 hex digest")
