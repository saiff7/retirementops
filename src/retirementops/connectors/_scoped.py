"""Shared connector plumbing: tenant-scoped transactions and provenance."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, text

from retirementops.clock import Clock, utc_now
from retirementops.domain.authz import TenantScope
from retirementops.domain.evidence import SourceRef, content_hash

__all__ = ["Clock", "make_source", "scoped_tx", "utc_now"]


@contextmanager
def scoped_tx(engine: Engine, scope: TenantScope) -> Iterator[Connection]:
    """Open a transaction whose RLS tenant scope is exactly `scope`.

    The setting is transaction-local (`set_config(..., true)`), so it cannot leak to the next user of a
    pooled connection. An empty scope makes every tenant-owned table appear empty.
    """
    with engine.begin() as conn:
        conn.execute(
            text("SELECT set_config('retirementops.tenant_ids', :v, true)"),
            {"v": scope.as_setting()},
        )
        yield conn


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
