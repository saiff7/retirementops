"""M3: agent run traces (spec §9 `agent_runs`, `tool_calls`, `evidence_items`) and their writer role.

Writer: `svc_agent_recorder`, a dedicated login role (owner decision 2026-11-26). It may only
INSERT and SELECT these three tables. It holds no UPDATE, DELETE, TRUNCATE or ownership, cannot
read any other table, and `app_service` gets nothing here.

Integrity, enforced by the database whatever the application does:
- Row-level security (`tenant_isolation`) on all three tables, like every tenant-owned table.
- `tool_calls` and `evidence_items` reference their run by (run_id, tenant_id), so a trace row can
  never be attached to another tenant's run.
- A trace row may only be written in the SAME transaction as its run (the run row's `xmin` is the
  current transaction). A finished run can never gain extra tool calls or evidence later.
- Rows are immutable: UPDATE, DELETE and TRUNCATE are blocked for every role, the owner included.
- Trace rows store hashes and sizes of tool arguments and results, never their values.

Downgrade drops the three tables (destroys traces) and revokes every grant made here.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-26
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

PIN = "SET search_path = pg_catalog, pg_temp"
OWNER_APP = (
    "current_user::text = (SELECT n.nspowner::regrole::text FROM pg_catalog.pg_namespace n "
    "WHERE n.nspname = 'app')"
)
QUEUES = (
    "'PAYROLL_OPERATIONS', 'ENROLLMENT_OPERATIONS', 'PLAN_ADMINISTRATION', 'DIGITAL_SUPPORT', "
    "'HUMAN_TRIAGE'"
)
TABLES = ("app.agent_runs", "app.tool_calls", "app.evidence_items")

UPGRADE = rf"""
CREATE TABLE app.agent_runs (
    run_id                 text PRIMARY KEY CHECK (run_id ~ '^RUN-[0-9A-F]{{16}}$'),
    tenant_id              text NOT NULL,
    ticket_id              text NOT NULL CHECK (ticket_id ~ '^ZD-[0-9]{{4,6}}$'),
    started_by             text NOT NULL CHECK (started_by ~ '^[a-z]+\.[a-z]+$'),
    correlation_id         text NOT NULL CHECK (length(correlation_id) BETWEEN 1 AND 64),
    skill_name             text NOT NULL CHECK (skill_name ~ '^[a-z0-9-]{{1,64}}$'),
    skill_version          text NOT NULL CHECK (skill_version ~ '^[a-z0-9-]{{1,64}}@[0-9]+\.[0-9]+\.[0-9]+$'),
    skill_sha256           char(64) NOT NULL,
    provider               text NOT NULL CHECK (provider ~ '^[a-z0-9_-]{{1,32}}$'),
    model_name             text NOT NULL CHECK (length(model_name) BETWEEN 1 AND 128),
    model_digest           text CHECK (model_digest IS NULL OR length(model_digest) <= 128),
    final_state            text NOT NULL CHECK (final_state IN ('PENDING_REVIEW', 'NEEDS_MANUAL_REVIEW')),
    state_path             jsonb NOT NULL CHECK (jsonb_typeof(state_path) = 'array'),
    state_reasons          jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(state_reasons) = 'array'),
    intent                 text,
    confidence             numeric(4, 3) CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    routed_queue           text CHECK (routed_queue IS NULL OR routed_queue IN ({QUEUES})),
    routing_rules_version  text,
    requires_manual_review boolean NOT NULL,
    finding_status         text,
    draft_id               text CHECK (draft_id IS NULL OR draft_id ~ '^DRF-[0-9A-F]{{16}}$'),
    proposal_id            text CHECK (proposal_id IS NULL OR proposal_id ~ '^PRP-[0-9A-F]{{16}}$'),
    model_turns            integer NOT NULL CHECK (model_turns BETWEEN 0 AND 100),
    tool_call_count        integer NOT NULL CHECK (tool_call_count BETWEEN 0 AND 200),
    started_at             timestamptz NOT NULL,
    finished_at            timestamptz NOT NULL CHECK (finished_at >= started_at),
    UNIQUE (run_id, tenant_id)
);
CREATE INDEX agent_runs_ticket ON app.agent_runs (tenant_id, ticket_id);

CREATE TABLE app.tool_calls (
    run_id         text NOT NULL,
    tenant_id      text NOT NULL,
    seq            integer NOT NULL CHECK (seq BETWEEN 1 AND 200),
    tool_name      text NOT NULL CHECK (tool_name ~ '^(<invalid>|[a-z_]{{1,64}})$'),
    decision       text NOT NULL CHECK (decision IN ('allow', 'deny', 'error')),
    outcome        text NOT NULL CHECK (outcome ~ '^[a-z_]{{1,40}}$'),
    args_sha256    char(64) NOT NULL,
    args_bytes     integer NOT NULL CHECK (args_bytes >= 0),
    result_sha256  char(64),
    latency_ms     numeric(10, 2) NOT NULL CHECK (latency_ms >= 0),
    called_at      timestamptz NOT NULL,
    PRIMARY KEY (run_id, seq),
    FOREIGN KEY (run_id, tenant_id) REFERENCES app.agent_runs (run_id, tenant_id)
);

CREATE TABLE app.evidence_items (
    run_id          text NOT NULL,
    tenant_id       text NOT NULL,
    seq             integer NOT NULL CHECK (seq BETWEEN 1 AND 100),
    system          text NOT NULL CHECK (system ~ '^[a-z_]{{1,32}}$'),
    record_type     text NOT NULL CHECK (record_type ~ '^[a-z_]{{1,32}}$'),
    record_id       text NOT NULL CHECK (length(record_id) BETWEEN 1 AND 64),
    record_version  text NOT NULL CHECK (length(record_version) BETWEEN 1 AND 32),
    content_hash    char(64) NOT NULL,
    retrieved_at    timestamptz NOT NULL,
    PRIMARY KEY (run_id, seq),
    UNIQUE (run_id, system, record_type, record_id),
    FOREIGN KEY (run_id, tenant_id) REFERENCES app.agent_runs (run_id, tenant_id)
);

-- A trace row belongs to a run written in this same transaction (append-once runs).
CREATE FUNCTION app.guard_trace_insert() RETURNS trigger
LANGUAGE plpgsql {PIN} AS $$
BEGIN
    IF {OWNER_APP} THEN
        RETURN NEW;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM app.agent_runs r
        WHERE r.run_id = NEW.run_id AND r.tenant_id = NEW.tenant_id
          AND r.xmin = pg_catalog.pg_current_xact_id()::xid
    ) THEN
        RAISE EXCEPTION 'trace rows must be written in the same transaction as their run'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER tool_calls_guard_insert BEFORE INSERT ON app.tool_calls
    FOR EACH ROW EXECUTE FUNCTION app.guard_trace_insert();
CREATE TRIGGER evidence_items_guard_insert BEFORE INSERT ON app.evidence_items
    FOR EACH ROW EXECUTE FUNCTION app.guard_trace_insert();

CREATE TRIGGER agent_runs_immutable BEFORE UPDATE OR DELETE ON app.agent_runs
    FOR EACH ROW EXECUTE FUNCTION app.immutable_row();
CREATE TRIGGER tool_calls_immutable BEFORE UPDATE OR DELETE ON app.tool_calls
    FOR EACH ROW EXECUTE FUNCTION app.immutable_row();
CREATE TRIGGER evidence_items_immutable BEFORE UPDATE OR DELETE ON app.evidence_items
    FOR EACH ROW EXECUTE FUNCTION app.immutable_row();
CREATE TRIGGER agent_runs_no_truncate BEFORE TRUNCATE ON app.agent_runs
    FOR EACH STATEMENT EXECUTE FUNCTION app.immutable_row();
CREATE TRIGGER tool_calls_no_truncate BEFORE TRUNCATE ON app.tool_calls
    FOR EACH STATEMENT EXECUTE FUNCTION app.immutable_row();
CREATE TRIGGER evidence_items_no_truncate BEFORE TRUNCATE ON app.evidence_items
    FOR EACH STATEMENT EXECUTE FUNCTION app.immutable_row();

-- Grants: the recorder only. Nothing for app_service or any other role.
GRANT USAGE ON SCHEMA app TO svc_agent_recorder;
GRANT USAGE ON SCHEMA sec TO svc_agent_recorder;
GRANT EXECUTE ON FUNCTION sec.tenant_scope() TO svc_agent_recorder;
GRANT SELECT, INSERT ON app.agent_runs, app.tool_calls, app.evidence_items TO svc_agent_recorder;
"""

DOWNGRADE = """
DROP TABLE IF EXISTS app.evidence_items, app.tool_calls, app.agent_runs;
DROP FUNCTION IF EXISTS app.guard_trace_insert();
REVOKE EXECUTE ON FUNCTION sec.tenant_scope() FROM svc_agent_recorder;
REVOKE USAGE ON SCHEMA sec FROM svc_agent_recorder;
REVOKE USAGE ON SCHEMA app FROM svc_agent_recorder;
"""


def upgrade() -> None:
    op.execute(UPGRADE)
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = ANY (sec.tenant_scope())) "
            "WITH CHECK (tenant_id = ANY (sec.tenant_scope()))"
        )


def downgrade() -> None:
    # WARNING: destroys every stored agent run trace.
    op.execute(DOWNGRADE)
