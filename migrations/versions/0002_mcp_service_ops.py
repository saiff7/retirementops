"""M2: service-ops policy knowledge base, drafts and action proposals.

- `servicekb_mock.policy_snippets`: provider-wide (not tenant-owned) fictional service policies,
  versioned and effective-dated. Read only, by `svc_servicekb_reader`.
- `app.drafts`, `app.draft_versions`, `app.action_proposals`: RetirementOps workflow metadata.
  `app_service` may INSERT and SELECT only. There is no UPDATE or DELETE grant: status transitions
  arrive with the validator (M3) and approvals (M5), each with its own narrow grant. Draft versions
  are immutable, enforced by trigger. All three tables are tenant-owned and use RLS.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-26
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SQL = r"""
CREATE SCHEMA servicekb_mock;

CREATE TABLE servicekb_mock.policy_snippets (
    snippet_id      text PRIMARY KEY CHECK (snippet_id ~ '^POL-[0-9]{3}$'),
    category        text NOT NULL CHECK (category IN ('CONTRIBUTIONS', 'PAYROLL_FILES', 'ENROLLMENT',
                        'ELIGIBILITY', 'PLAN_RULES', 'PORTAL_ACCESS', 'PRIVACY', 'COMMUNICATIONS',
                        'ESCALATION')),
    title           text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
    body            text NOT NULL CHECK (length(body) BETWEEN 1 AND 4000),
    version         text NOT NULL CHECK (version ~ '^v[0-9]+$'),
    effective_from  date NOT NULL,
    effective_to    date,
    is_fictional    boolean NOT NULL DEFAULT true CHECK (is_fictional),
    record_version  integer NOT NULL DEFAULT 1 CHECK (record_version > 0),
    CHECK (effective_to IS NULL OR effective_to > effective_from)
);

-- Drafts: header + immutable versions. Status is fail-closed: until the server-side validator
-- exists (M3), every draft is NEEDS_MANUAL_REVIEW.
CREATE TABLE app.drafts (
    draft_id        text PRIMARY KEY CHECK (draft_id ~ '^DRF-[0-9A-F]{16}$'),
    ticket_id       text NOT NULL CHECK (ticket_id ~ '^ZD-[0-9]{4,6}$'),
    tenant_id       text NOT NULL,
    status          text NOT NULL CHECK (status IN ('NEEDS_MANUAL_REVIEW', 'PENDING_REVIEW',
                        'APPROVED', 'REJECTED', 'ESCALATED', 'EXECUTED', 'EXECUTION_FAILED')),
    status_reasons  jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_by      text NOT NULL,
    created_at      timestamptz NOT NULL,
    UNIQUE (draft_id, tenant_id)
);
CREATE INDEX drafts_ticket ON app.drafts (tenant_id, ticket_id);

CREATE TABLE app.draft_versions (
    draft_id          text NOT NULL,
    tenant_id         text NOT NULL,
    version           integer NOT NULL CHECK (version > 0),
    body              text NOT NULL CHECK (length(body) BETWEEN 1 AND 10000),
    evidence_refs     jsonb NOT NULL,
    skill_version     text NOT NULL CHECK (skill_version ~ '^[a-z0-9-]{1,64}@[0-9]+\.[0-9]+\.[0-9]+$'),
    ticket_version    integer NOT NULL CHECK (ticket_version > 0),
    content_hash      char(64) NOT NULL,
    author_principal  text NOT NULL,
    author_kind       text NOT NULL CHECK (author_kind IN ('human', 'service')),
    created_at        timestamptz NOT NULL,
    PRIMARY KEY (draft_id, version),
    FOREIGN KEY (draft_id, tenant_id) REFERENCES app.drafts (draft_id, tenant_id)
);

CREATE TABLE app.action_proposals (
    proposal_id       text PRIMARY KEY CHECK (proposal_id ~ '^PRP-[0-9A-F]{16}$'),
    tenant_id         text NOT NULL,
    ticket_id         text NOT NULL CHECK (ticket_id ~ '^ZD-[0-9]{4,6}$'),
    action_type       text NOT NULL CHECK (action_type = 'ASSIGN_QUEUE'),
    target_queue      text NOT NULL CHECK (target_queue IN ('PAYROLL_OPERATIONS',
                          'ENROLLMENT_OPERATIONS', 'PLAN_ADMINISTRATION', 'DIGITAL_SUPPORT',
                          'HUMAN_TRIAGE')),
    rationale         text NOT NULL CHECK (length(rationale) BETWEEN 1 AND 1000),
    status            text NOT NULL CHECK (status IN ('PROPOSED', 'APPROVED', 'REJECTED', 'EXECUTED')),
    ticket_version    integer NOT NULL CHECK (ticket_version > 0),
    content_hash      char(64) NOT NULL,
    created_by        text NOT NULL,
    created_at        timestamptz NOT NULL
);
-- At most one open proposal per (tenant, ticket, action, target): retries are deduplicated.
CREATE UNIQUE INDEX action_proposals_open_unique ON app.action_proposals (tenant_id, content_hash)
    WHERE status = 'PROPOSED';

CREATE FUNCTION app.immutable_row() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is immutable (% blocked)', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'insufficient_privilege';
END
$$;
CREATE TRIGGER draft_versions_immutable BEFORE UPDATE OR DELETE ON app.draft_versions
    FOR EACH ROW EXECUTE FUNCTION app.immutable_row();

-- Grants
GRANT USAGE ON SCHEMA sec TO svc_servicekb_reader;
GRANT EXECUTE ON FUNCTION sec.tenant_scope() TO svc_servicekb_reader;
GRANT USAGE ON SCHEMA servicekb_mock TO svc_servicekb_reader;
GRANT SELECT ON servicekb_mock.policy_snippets TO svc_servicekb_reader;

GRANT SELECT, INSERT ON app.drafts, app.draft_versions, app.action_proposals TO app_service;
"""

RLS_TABLES = ("app.drafts", "app.draft_versions", "app.action_proposals")


def upgrade() -> None:
    op.execute(SQL)
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = ANY (sec.tenant_scope())) "
            "WITH CHECK (tenant_id = ANY (sec.tenant_scope()))"
        )


def downgrade() -> None:
    # WARNING: destroys every draft and proposal (see docs/operations-migrations.md).
    op.execute("DROP TABLE IF EXISTS app.action_proposals, app.draft_versions, app.drafts CASCADE")
    # Revoke what upgrade() granted outside the dropped schema (review Low-3).
    op.execute("REVOKE EXECUTE ON FUNCTION sec.tenant_scope() FROM svc_servicekb_reader")
    op.execute("REVOKE USAGE ON SCHEMA sec FROM svc_servicekb_reader")
    op.execute("DROP FUNCTION IF EXISTS app.immutable_row()")
    op.execute("DROP SCHEMA IF EXISTS servicekb_mock CASCADE")
