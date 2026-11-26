"""Tenant-scoped transactions for every role whose tables use row-level security (ADR-003)."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, text

from retirementops.domain.authz import TenantScope


@contextmanager
def scoped_tx(engine: Engine, scope: TenantScope) -> Iterator[Connection]:
    """Open a transaction whose RLS tenant scope is exactly `scope`.

    The setting is transaction-local (`set_config(..., true)`), so it cannot leak to the next user of
    a pooled connection. An empty scope makes every tenant-owned table appear empty.
    """
    with engine.begin() as conn:
        conn.execute(
            text("SELECT set_config('retirementops.tenant_ids', :v, true)"),
            {"v": scope.as_setting()},
        )
        yield conn
