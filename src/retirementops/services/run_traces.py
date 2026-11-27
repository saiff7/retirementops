"""Writes finished agent-run traces through the dedicated recorder role (migration 0007).

The trace record types are re-exported here so the agent runtime never imports the store.
"""

from __future__ import annotations

from collections.abc import Sequence

from retirementops.domain.authz import TenantScope
from retirementops.store.traces import (
    EvidenceTrace,
    RunHeader,
    RunTraceStore,
    StoredRun,
    ToolCallTrace,
)


class RunTraceService:
    def __init__(self, store: RunTraceStore) -> None:
        self._store = store

    def record(
        self, header: RunHeader, calls: Sequence[ToolCallTrace], evidence: Sequence[EvidenceTrace]
    ) -> None:
        self._store.write_run(header, calls, evidence)

    def get(self, tenant_id: str, run_id: str) -> StoredRun | None:
        return self._store.get_run(TenantScope(frozenset({tenant_id})), run_id)


__all__ = ["EvidenceTrace", "RunHeader", "RunTraceService", "StoredRun", "ToolCallTrace"]
