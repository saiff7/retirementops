"""Load a generated dataset as `ro_owner`, standing in for each mock system's own data entry.

The owner role bypasses RLS because it owns the tables; runtime roles never do. `db_table_hash` reads
everything back so a test can prove the database holds exactly what the generator produced.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, column, insert, select, table
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.types import String

from retirementops.seed.generator import Dataset, table_hash

# Identity columns (row_id, comment_id, event_id) are assigned by Postgres. The generator does not
# produce them and db_table_hash selects only generated columns, so they never affect comparison.
_TYPED: dict[str, Any] = {"details": JSONB, "roles": ARRAY(String), "tenant_ids": ARRAY(String)}


def _table(qualified: str, columns: list[str]) -> Any:
    schema, name = qualified.split(".")
    return table(name, *[column(c, _TYPED.get(c)) for c in columns], schema=schema)


def load_dataset(owner_engine: Engine, dataset: Dataset) -> None:
    with owner_engine.begin() as conn:
        for qualified, rows in dataset.tables.items():
            if rows:
                conn.execute(insert(_table(qualified, list(rows[0].keys()))), rows)


def db_table_hash(owner_engine: Engine, dataset: Dataset) -> str:
    out: dict[str, list[dict[str, Any]]] = {}
    with owner_engine.connect() as conn:
        for qualified, rows in dataset.tables.items():
            cols = list(rows[0].keys()) if rows else []
            if not cols:
                out[qualified] = []
                continue
            t = _table(qualified, cols)
            out[qualified] = [dict(r) for r in conn.execute(select(*t.c)).mappings()]
    return table_hash(out)
