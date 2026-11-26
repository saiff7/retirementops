"""M2 review fixes: database-level integrity for drafts and proposals.

H-1:  a runtime role may create drafts only in an initial state (NEEDS_MANUAL_REVIEW, PENDING_REVIEW),
      proposals only as PROPOSED, and only version 1 of a draft, written by the draft's creator in
      the same transaction as the draft header. Later versions, status changes and approvals belong
      to the separate review role introduced in M5 (ADR-013). The schema owner (migrations, seed) is
      exempt.
Low-1: TRUNCATE is blocked on all three workflow tables (UPDATE/DELETE on versions already was).
Low-8: optional per-tenant idempotency key on drafts, so an agent retry returns the original draft.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-26
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

OWNER_CHECK = (
    "current_user::text = (SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname = 'app')"
)

UPGRADE = rf"""
ALTER TABLE app.drafts ADD COLUMN idempotency_key text
    CHECK (idempotency_key ~ '^[A-Za-z0-9:_-]{{8,128}}$');
CREATE UNIQUE INDEX drafts_idempotency ON app.drafts (tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE FUNCTION app.guard_draft_insert() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT ({OWNER_CHECK}) AND NEW.status NOT IN ('NEEDS_MANUAL_REVIEW', 'PENDING_REVIEW') THEN
        RAISE EXCEPTION 'drafts can only be created as NEEDS_MANUAL_REVIEW or PENDING_REVIEW (got %)',
            NEW.status USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER drafts_guard_insert BEFORE INSERT ON app.drafts
    FOR EACH ROW EXECUTE FUNCTION app.guard_draft_insert();

CREATE FUNCTION app.guard_draft_version_insert() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF {OWNER_CHECK} THEN
        RETURN NEW;
    END IF;
    IF NEW.version <> 1 THEN
        RAISE EXCEPTION 'only version 1 can be written by this role; later versions belong to the review role'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    -- Version 1 must be written by the draft's creator, together with the header (same timestamp).
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
CREATE TRIGGER draft_versions_guard_insert BEFORE INSERT ON app.draft_versions
    FOR EACH ROW EXECUTE FUNCTION app.guard_draft_version_insert();

CREATE FUNCTION app.guard_proposal_insert() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NOT ({OWNER_CHECK}) AND NEW.status <> 'PROPOSED' THEN
        RAISE EXCEPTION 'proposals can only be created as PROPOSED (got %)', NEW.status
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER action_proposals_guard_insert BEFORE INSERT ON app.action_proposals
    FOR EACH ROW EXECUTE FUNCTION app.guard_proposal_insert();

CREATE TRIGGER drafts_no_truncate BEFORE TRUNCATE ON app.drafts
    FOR EACH STATEMENT EXECUTE FUNCTION app.immutable_row();
CREATE TRIGGER draft_versions_no_truncate BEFORE TRUNCATE ON app.draft_versions
    FOR EACH STATEMENT EXECUTE FUNCTION app.immutable_row();
CREATE TRIGGER action_proposals_no_truncate BEFORE TRUNCATE ON app.action_proposals
    FOR EACH STATEMENT EXECUTE FUNCTION app.immutable_row();
"""

DOWNGRADE = """
DROP TRIGGER IF EXISTS action_proposals_no_truncate ON app.action_proposals;
DROP TRIGGER IF EXISTS draft_versions_no_truncate ON app.draft_versions;
DROP TRIGGER IF EXISTS drafts_no_truncate ON app.drafts;
DROP TRIGGER IF EXISTS action_proposals_guard_insert ON app.action_proposals;
DROP TRIGGER IF EXISTS draft_versions_guard_insert ON app.draft_versions;
DROP TRIGGER IF EXISTS drafts_guard_insert ON app.drafts;
DROP FUNCTION IF EXISTS app.guard_proposal_insert();
DROP FUNCTION IF EXISTS app.guard_draft_version_insert();
DROP FUNCTION IF EXISTS app.guard_draft_insert();
DROP INDEX IF EXISTS app.drafts_idempotency;
ALTER TABLE app.drafts DROP COLUMN IF EXISTS idempotency_key;
"""


def upgrade() -> None:
    op.execute(UPGRADE)


def downgrade() -> None:
    op.execute(DOWNGRADE)
