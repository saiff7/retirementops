"""M2 final-review fixes (N-1 and related lows).

N-1:   idempotency keys are unique per (tenant_id, created_by, idempotency_key): one user's key never
       matches another user's request.
Low-2: drafts.created_by_kind ('human' | 'service') is recorded on the header, and version 1 must use
       the same author_kind. In M2, runtime roles may only insert drafts as NEEDS_MANUAL_REVIEW;
       PENDING_REVIEW is re-allowed in M3 together with the server-side validator (ADR-013).
Low-3: every guard trigger function pins `search_path = pg_catalog, pg_temp` and uses
       `pg_catalog.pg_namespace`, including the M1 ticket-status guard (0001).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-26
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

PIN = "SET search_path = pg_catalog, pg_temp"


def _owner(schema: str) -> str:
    return (
        "current_user::text = (SELECT n.nspowner::regrole::text FROM pg_catalog.pg_namespace n "
        f"WHERE n.nspname = '{schema}')"
    )


UPGRADE = f"""
-- N-1: per-user idempotency keys.
DROP INDEX app.drafts_idempotency;
CREATE UNIQUE INDEX drafts_idempotency_per_user ON app.drafts (tenant_id, created_by, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- Low-2: author kind on the header (backfilled from version 1; orphans conservatively 'service').
ALTER TABLE app.drafts ADD COLUMN created_by_kind text CHECK (created_by_kind IN ('human', 'service'));
UPDATE app.drafts d SET created_by_kind = v.author_kind
    FROM app.draft_versions v WHERE v.draft_id = d.draft_id AND v.version = 1;
UPDATE app.drafts SET created_by_kind = 'service' WHERE created_by_kind IS NULL;
ALTER TABLE app.drafts ALTER COLUMN created_by_kind SET NOT NULL;

CREATE OR REPLACE FUNCTION app.guard_draft_insert() RETURNS trigger
LANGUAGE plpgsql {PIN} AS $$
BEGIN
    -- M2: only NEEDS_MANUAL_REVIEW. M3 adds PENDING_REVIEW together with the server-side validator.
    IF NOT ({_owner("app")}) AND NEW.status <> 'NEEDS_MANUAL_REVIEW' THEN
        RAISE EXCEPTION 'drafts can only be created as NEEDS_MANUAL_REVIEW (got %)', NEW.status
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION app.guard_draft_version_insert() RETURNS trigger
LANGUAGE plpgsql {PIN} AS $$
BEGIN
    IF {_owner("app")} THEN
        RETURN NEW;
    END IF;
    IF NEW.version <> 1 THEN
        RAISE EXCEPTION 'only version 1 can be written by this role; later versions belong to the review role'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM app.drafts d
        WHERE d.draft_id = NEW.draft_id AND d.tenant_id = NEW.tenant_id
          AND d.created_by = NEW.author_principal AND d.created_by_kind = NEW.author_kind
          AND d.created_at = NEW.created_at
    ) THEN
        RAISE EXCEPTION 'version 1 must match its draft header (creator, creator kind and creation time)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION app.guard_proposal_insert() RETURNS trigger
LANGUAGE plpgsql {PIN} AS $$
BEGIN
    IF NOT ({_owner("app")}) AND NEW.status <> 'PROPOSED' THEN
        RAISE EXCEPTION 'proposals can only be created as PROPOSED (got %)', NEW.status
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION zendesk_mock.guard_ticket_status() RETURNS trigger
LANGUAGE plpgsql {PIN} AS $$
BEGIN
    IF NEW.status IS DISTINCT FROM OLD.status AND NOT ({_owner("zendesk_mock")}) THEN
        RAISE EXCEPTION 'ticket status can only change inside the ticketing system (role %)', current_user
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.ticket_id IS DISTINCT FROM OLD.ticket_id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id THEN
        RAISE EXCEPTION 'ticket identity is immutable' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;

ALTER FUNCTION app.immutable_row() {PIN};
ALTER FUNCTION app.audit_append_only() {PIN};
"""

# Restores the 0003 (and 0001) definitions exactly, including their unpinned search paths.
OLD_OWNER_APP = (
    "current_user::text = (SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname = 'app')"
)
DOWNGRADE = f"""
ALTER FUNCTION app.audit_append_only() RESET search_path;
ALTER FUNCTION app.immutable_row() RESET search_path;

CREATE OR REPLACE FUNCTION zendesk_mock.guard_ticket_status() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status IS DISTINCT FROM OLD.status
       AND current_user::text <> (SELECT nspowner::regrole::text FROM pg_namespace
                                  WHERE nspname = 'zendesk_mock') THEN
        RAISE EXCEPTION 'ticket status can only change inside the ticketing system (role %)', current_user
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.ticket_id IS DISTINCT FROM OLD.ticket_id OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id THEN
        RAISE EXCEPTION 'ticket identity is immutable' USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
ALTER FUNCTION zendesk_mock.guard_ticket_status() RESET search_path;

CREATE OR REPLACE FUNCTION app.guard_proposal_insert() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT ({OLD_OWNER_APP}) AND NEW.status <> 'PROPOSED' THEN
        RAISE EXCEPTION 'proposals can only be created as PROPOSED (got %)', NEW.status
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
ALTER FUNCTION app.guard_proposal_insert() RESET search_path;

CREATE OR REPLACE FUNCTION app.guard_draft_version_insert() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF {OLD_OWNER_APP} THEN
        RETURN NEW;
    END IF;
    IF NEW.version <> 1 THEN
        RAISE EXCEPTION 'only version 1 can be written by this role; later versions belong to the review role'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM app.drafts d
        WHERE d.draft_id = NEW.draft_id AND d.tenant_id = NEW.tenant_id
          AND d.created_by = NEW.author_principal AND d.created_at = NEW.created_at
    ) THEN
        RAISE EXCEPTION 'version 1 must match its draft header (creator and creation time)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
ALTER FUNCTION app.guard_draft_version_insert() RESET search_path;

CREATE OR REPLACE FUNCTION app.guard_draft_insert() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT ({OLD_OWNER_APP}) AND NEW.status NOT IN ('NEEDS_MANUAL_REVIEW', 'PENDING_REVIEW') THEN
        RAISE EXCEPTION 'drafts can only be created as NEEDS_MANUAL_REVIEW or PENDING_REVIEW (got %)',
            NEW.status USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
ALTER FUNCTION app.guard_draft_insert() RESET search_path;

ALTER TABLE app.drafts DROP COLUMN created_by_kind;
-- Per-user keys may collide per tenant; keep the oldest draft's key so the 0003 index can be rebuilt.
-- Only retry metadata is cleared: no draft or version is deleted.
UPDATE app.drafts d SET idempotency_key = NULL
WHERE d.idempotency_key IS NOT NULL AND EXISTS (
    SELECT 1 FROM app.drafts e
    WHERE e.tenant_id = d.tenant_id AND e.idempotency_key = d.idempotency_key
      AND (e.created_at, e.draft_id) < (d.created_at, d.draft_id)
);
DROP INDEX app.drafts_idempotency_per_user;
CREATE UNIQUE INDEX drafts_idempotency ON app.drafts (tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
