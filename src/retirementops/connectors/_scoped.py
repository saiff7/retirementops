"""Shared connector plumbing: tenant-scoped transactions and provenance."""

from __future__ import annotations

from retirementops.clock import Clock, utc_now
from retirementops.db.scoped import scoped_tx
from retirementops.domain.evidence import SourceRef, content_hash

__all__ = ["Clock", "make_source", "scoped_tx", "utc_now"]


def make_source(
    *,
    system: str,
    record_type: str,
    record_id: str,
    record_version: object,
    payload: dict[str, object],
    clock: Clock,
) -> SourceRef:
    return SourceRef(
        system=system,
        record_type=record_type,
        record_id=record_id,
        record_version=str(record_version),
        retrieved_at=clock(),
        content_hash=content_hash(payload),
    )
