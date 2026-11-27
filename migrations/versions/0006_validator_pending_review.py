"""M3: re-allow PENDING_REVIEW at draft creation, together with the server-side validator (ADR-013).

- `app.drafts.validator_version` records which validator judged the draft (NULL for drafts made
  before M3).
- `app.guard_draft_insert` (runtime roles only) now accepts PENDING_REVIEW, but only when the row
  names a validator version and carries no outstanding reasons. NEEDS_MANUAL_REVIEW stays allowed.
  Every later state (APPROVED, ...) is still refused. The function keeps the pinned
  `search_path = pg_catalog, pg_temp` and the schema-qualified owner check from 0004.
- No grants change: `app_service` already holds table-level INSERT on `app.drafts` (0002), which
  covers the new column. Nothing is widened.

Downgrade restores the exact 0004 function and drops the column (validator versions of existing
drafts are lost; the drafts themselves are kept).

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-26
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

PIN = "SET search_path = pg_catalog, pg_temp"
OWNER_APP = (
    "current_user::text = (SELECT n.nspowner::regrole::text FROM pg_catalog.pg_namespace n "
    "WHERE n.nspname = 'app')"
)

UPGRADE = f"""
ALTER TABLE app.drafts ADD COLUMN validator_version text
    CHECK (validator_version IS NULL OR validator_version ~ '^[a-z0-9-]{{1,64}}/[0-9]{{1,6}}$');

CREATE OR REPLACE FUNCTION app.guard_draft_insert() RETURNS trigger
LANGUAGE plpgsql {PIN} AS $$
BEGIN
    IF {OWNER_APP} THEN
        RETURN NEW;
    END IF;
    IF NEW.status = 'NEEDS_MANUAL_REVIEW' THEN
        RETURN NEW;
    END IF;
    -- M3: PENDING_REVIEW only for a draft the server-side validator passed.
    IF NEW.status = 'PENDING_REVIEW' THEN
        IF NEW.validator_version IS NULL OR NEW.status_reasons <> '[]'::jsonb THEN
            RAISE EXCEPTION 'PENDING_REVIEW requires a passing validator (version set, no reasons)'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'drafts can only be created as NEEDS_MANUAL_REVIEW or PENDING_REVIEW (got %)',
        NEW.status USING ERRCODE = 'insufficient_privilege';
END
$$;
"""

# The exact 0004 definition.
DOWNGRADE = f"""
CREATE OR REPLACE FUNCTION app.guard_draft_insert() RETURNS trigger
LANGUAGE plpgsql {PIN} AS $$
BEGIN
    -- M2: only NEEDS_MANUAL_REVIEW. M3 adds PENDING_REVIEW together with the server-side validator.
    IF NOT ({OWNER_APP}) AND NEW.status <> 'NEEDS_MANUAL_REVIEW' THEN
        RAISE EXCEPTION 'drafts can only be created as NEEDS_MANUAL_REVIEW (got %)', NEW.status
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;

ALTER TABLE app.drafts DROP COLUMN validator_version;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
