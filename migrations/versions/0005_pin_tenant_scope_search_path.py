"""Pin sec.tenant_scope()'s search_path (final M2 hardening; ADR-003, ADR-013).

`sec.tenant_scope()` backs all 14 tenant row-level security policies. Until now it resolved
`current_setting`, `string_to_array`, the `text` type and the `=` operator through the CALLER's
search_path. A role able to create objects in some schema could shadow them and widen its own tenant
scope. Runtime roles cannot create objects anywhere (no CREATE, no TEMP), so this was not exploitable,
but RLS should not depend on that.

Upgrade keeps everything except name resolution exactly as it was:
- language sql, STABLE, PARALLEL SAFE, SECURITY INVOKER, not STRICT, not LEAKPROOF, RETURNS text[];
- owner and EXECUTE grants (CREATE OR REPLACE preserves both);
- the result for every input, including unset, '', ' ' and empty list elements.

It adds `SET search_path = pg_catalog` and schema-qualifies every function, type and operator.
Trade-off: Postgres does not inline SQL functions that carry SET clauses, so each RLS check calls the
function. At demo scale this is negligible; revisit with EXPLAIN if row counts grow.

Downgrade restores the exact 0001 definition and clears the function's configuration.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-26
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

UPGRADE = """
CREATE OR REPLACE FUNCTION sec.tenant_scope() RETURNS pg_catalog.text[]
LANGUAGE sql STABLE PARALLEL SAFE
SET search_path = pg_catalog
AS $$
    SELECT CASE
        WHEN coalesce(pg_catalog.current_setting('retirementops.tenant_ids', true), '')
             OPERATOR(pg_catalog.=) ''
            THEN ARRAY[]::pg_catalog.text[]
        ELSE pg_catalog.string_to_array(
            pg_catalog.current_setting('retirementops.tenant_ids', true), ',')
    END
$$;
"""

# The exact body created by migration 0001 (whitespace included), with no configuration.
DOWNGRADE = """
CREATE OR REPLACE FUNCTION sec.tenant_scope() RETURNS text[]
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT CASE
        WHEN coalesce(current_setting('retirementops.tenant_ids', true), '') = ''
            THEN ARRAY[]::text[]
        ELSE string_to_array(current_setting('retirementops.tenant_ids', true), ',')
    END
$$;
ALTER FUNCTION sec.tenant_scope() RESET ALL;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
