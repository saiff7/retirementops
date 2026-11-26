"""Initial schemas: mock systems, app metadata, per-connector grants, RLS, audit protection.

See ADR-003 (schemas, roles, RLS), ADR-006 (integer cents), ADR-007 (no status mutation),
ADR-009 (append-only audit). All objects are owned by ro_owner, the role running this migration.

Revision ID: 0001
Revises:
Create Date: 2026-11-26
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

RUNTIME_ROLES = (
    "app_service",
    "svc_zendesk_reader",
    "svc_zendesk_writer",
    "svc_planadmin_reader",
    "svc_payroll_reader",
    "svc_payroll_ingest",
)

# Tenant-owned tables that get row-level security.
RLS_TABLES = (
    "zendesk_mock.organizations",
    "zendesk_mock.tickets",
    "zendesk_mock.ticket_comments",
    "zendesk_mock.ticket_events",
    "planadmin_mock.tenants",
    "planadmin_mock.plans",
    "planadmin_mock.plan_rules",
    "planadmin_mock.participants",
    "planadmin_mock.enrollments",
    "payroll_mock.payroll_files",
    "payroll_mock.payroll_rows",
)

SCHEMAS_SQL = """
REVOKE ALL ON SCHEMA public FROM PUBLIC;

CREATE SCHEMA sec;
CREATE SCHEMA zendesk_mock;
CREATE SCHEMA planadmin_mock;
CREATE SCHEMA payroll_mock;
CREATE SCHEMA app;

-- Tenant scope for RLS. Reads a transaction-local setting written by connectors from the
-- verified principal. Unset or empty means an empty scope, so no rows are visible (fail closed).
CREATE FUNCTION sec.tenant_scope() RETURNS text[]
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    SELECT CASE
        WHEN coalesce(current_setting('retirementops.tenant_ids', true), '') = ''
            THEN ARRAY[]::text[]
        ELSE string_to_array(current_setting('retirementops.tenant_ids', true), ',')
    END
$$;
REVOKE ALL ON FUNCTION sec.tenant_scope() FROM PUBLIC;
"""

PLANADMIN_SQL = r"""
CREATE TABLE planadmin_mock.tenants (
    tenant_id       text PRIMARY KEY CHECK (tenant_id ~ '^TEN-[A-Z0-9]{2,12}$'),
    display_name    text NOT NULL CHECK (length(display_name) <= 120),
    record_version  integer NOT NULL DEFAULT 1 CHECK (record_version > 0),
    updated_at      timestamptz NOT NULL
);

CREATE TABLE planadmin_mock.plans (
    plan_id         text PRIMARY KEY CHECK (plan_id ~ '^PLAN-[0-9]{3}$'),
    tenant_id       text NOT NULL REFERENCES planadmin_mock.tenants (tenant_id),
    name            text NOT NULL CHECK (length(name) <= 120),
    currency        char(3) NOT NULL DEFAULT 'USD' CHECK (currency = 'USD'),
    record_version  integer NOT NULL DEFAULT 1 CHECK (record_version > 0),
    updated_at      timestamptz NOT NULL,
    UNIQUE (plan_id, tenant_id)
);

-- Fictional, effective-dated demonstration rules. Overlaps are deliberately NOT prevented here:
-- the domain detects them as RULE_CONFLICT (a seeded difficult case).
CREATE TABLE planadmin_mock.plan_rules (
    rule_id            text PRIMARY KEY CHECK (rule_id ~ '^RULE-[0-9]{3,4}$'),
    plan_id            text NOT NULL,
    tenant_id          text NOT NULL,
    contribution_type  text NOT NULL CHECK (contribution_type IN ('EMPLOYEE_DEFERRAL', 'EMPLOYER_MATCH')),
    rule_version       text NOT NULL CHECK (rule_version ~ '^v[0-9]+$'),
    formula_code       text NOT NULL CHECK (formula_code IN ('ELECTED_PCT_OF_ELIGIBLE_PAY', 'MATCH_RATE_UP_TO_CAP_PCT')),
    max_deferral_pct   numeric(7,4) CHECK (max_deferral_pct BETWEEN 0 AND 100),
    match_rate         numeric(7,4) CHECK (match_rate BETWEEN 0 AND 10),
    match_cap_pct      numeric(7,4) CHECK (match_cap_pct BETWEEN 0 AND 100),
    rounding           text NOT NULL DEFAULT 'ROUND_HALF_UP' CHECK (rounding = 'ROUND_HALF_UP'),
    effective_from     date NOT NULL,
    effective_to       date,
    is_fictional       boolean NOT NULL DEFAULT true CHECK (is_fictional),
    record_version     integer NOT NULL DEFAULT 1 CHECK (record_version > 0),
    FOREIGN KEY (plan_id, tenant_id) REFERENCES planadmin_mock.plans (plan_id, tenant_id),
    CHECK (effective_to IS NULL OR effective_to > effective_from),
    CHECK (
        (contribution_type = 'EMPLOYEE_DEFERRAL' AND formula_code = 'ELECTED_PCT_OF_ELIGIBLE_PAY'
            AND max_deferral_pct IS NOT NULL AND match_rate IS NULL AND match_cap_pct IS NULL)
     OR (contribution_type = 'EMPLOYER_MATCH' AND formula_code = 'MATCH_RATE_UP_TO_CAP_PCT'
            AND max_deferral_pct IS NULL AND match_rate IS NOT NULL AND match_cap_pct IS NOT NULL)
    )
);
CREATE INDEX plan_rules_lookup ON planadmin_mock.plan_rules (plan_id, contribution_type, effective_from);

-- Synthetic aliases only. No names, dates of birth, addresses or government IDs exist anywhere.
CREATE TABLE planadmin_mock.participants (
    participant_ref        text PRIMARY KEY CHECK (participant_ref ~ '^PART-[0-9]{4}$'),
    tenant_id              text NOT NULL REFERENCES planadmin_mock.tenants (tenant_id),
    employer_employee_ref  text NOT NULL CHECK (employer_employee_ref ~ '^E-[0-9]{5}$'),
    record_version         integer NOT NULL DEFAULT 1 CHECK (record_version > 0),
    updated_at             timestamptz NOT NULL,
    UNIQUE (participant_ref, tenant_id),
    UNIQUE (tenant_id, employer_employee_ref)
);

CREATE TABLE planadmin_mock.enrollments (
    enrollment_id    text PRIMARY KEY CHECK (enrollment_id ~ '^ENR-[0-9]{4,5}$'),
    participant_ref  text NOT NULL,
    plan_id          text NOT NULL,
    tenant_id        text NOT NULL,
    status           text NOT NULL CHECK (status IN ('ENROLLED', 'NOT_ENROLLED', 'OPTED_OUT', 'PENDING')),
    deferral_pct     numeric(7,4) NOT NULL CHECK (deferral_pct BETWEEN 0 AND 100),
    effective_from   date NOT NULL,
    effective_to     date,
    record_version   integer NOT NULL DEFAULT 1 CHECK (record_version > 0),
    updated_at       timestamptz NOT NULL,
    -- Composite FKs make a cross-tenant enrollment impossible at the database level.
    FOREIGN KEY (participant_ref, tenant_id) REFERENCES planadmin_mock.participants (participant_ref, tenant_id),
    FOREIGN KEY (plan_id, tenant_id) REFERENCES planadmin_mock.plans (plan_id, tenant_id),
    CHECK (effective_to IS NULL OR effective_to > effective_from)
);
CREATE INDEX enrollments_lookup ON planadmin_mock.enrollments (participant_ref, plan_id);
"""

PAYROLL_SQL = r"""
CREATE TABLE payroll_mock.pay_periods (
    pay_period_id  text PRIMARY KEY CHECK (pay_period_id ~ '^PP-[0-9]{4}-[0-9]{2}$'),
    period_start   date NOT NULL,
    period_end     date NOT NULL,
    pay_date       date NOT NULL,
    CHECK (period_end >= period_start),
    CHECK (pay_date >= period_end)
);

CREATE TABLE payroll_mock.payroll_files (
    file_id        text PRIMARY KEY CHECK (file_id ~ '^PF-[0-9A-F]{12}$'),
    tenant_id      text NOT NULL,
    plan_id        text NOT NULL,
    pay_period_id  text NOT NULL REFERENCES payroll_mock.pay_periods (pay_period_id),
    received_at    timestamptz NOT NULL,
    file_sha256    char(64) NOT NULL,
    row_count      integer NOT NULL CHECK (row_count >= 0),
    source         text NOT NULL CHECK (source IN ('seed', 'upload')),
    UNIQUE (file_id, tenant_id, plan_id, pay_period_id)
);

-- Money is stored as integer cents (ADR-006).
CREATE TABLE payroll_mock.payroll_rows (
    row_id                        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_id                       text NOT NULL,
    tenant_id                     text NOT NULL,
    plan_id                       text NOT NULL,
    pay_period_id                 text NOT NULL,
    participant_ref               text NOT NULL CHECK (participant_ref ~ '^PART-[0-9]{4}$'),
    eligible_pay_cents            bigint NOT NULL CHECK (eligible_pay_cents BETWEEN 0 AND 100000000),
    employee_contribution_cents   bigint NOT NULL CHECK (employee_contribution_cents BETWEEN 0 AND 100000000),
    employer_contribution_cents   bigint NOT NULL CHECK (employer_contribution_cents BETWEEN 0 AND 100000000),
    currency                      char(3) NOT NULL DEFAULT 'USD' CHECK (currency = 'USD'),
    FOREIGN KEY (file_id, tenant_id, plan_id, pay_period_id)
        REFERENCES payroll_mock.payroll_files (file_id, tenant_id, plan_id, pay_period_id)
);
CREATE INDEX payroll_rows_lookup ON payroll_mock.payroll_rows (participant_ref, plan_id, pay_period_id);
"""

ZENDESK_SQL = r"""
CREATE TABLE zendesk_mock.organizations (
    org_id     text PRIMARY KEY CHECK (org_id ~ '^ORG-[0-9]{3}$'),
    tenant_id  text NOT NULL UNIQUE,            -- assumption A1: 1 org <-> 1 tenant
    name       text NOT NULL CHECK (length(name) <= 120),
    UNIQUE (org_id, tenant_id)
);

CREATE TABLE zendesk_mock.tickets (
    ticket_id        text PRIMARY KEY CHECK (ticket_id ~ '^ZD-[0-9]{4,6}$'),
    org_id           text NOT NULL,
    tenant_id        text NOT NULL,
    requester_email  text NOT NULL CHECK (requester_email ~ '@[a-z0-9.-]+\.example\.test$'),
    subject          text NOT NULL CHECK (length(subject) BETWEEN 1 AND 300),
    body             text NOT NULL CHECK (length(body) BETWEEN 1 AND 20000),
    status           text NOT NULL CHECK (status IN ('new', 'open', 'pending', 'solved', 'closed')),
    priority         text NOT NULL CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    assignee_queue   text CHECK (assignee_queue IN ('PAYROLL_OPERATIONS', 'ENROLLMENT_OPERATIONS',
                         'PLAN_ADMINISTRATION', 'DIGITAL_SUPPORT', 'HUMAN_TRIAGE')),
    channel          text NOT NULL CHECK (channel IN ('email', 'portal', 'phone_note')),
    created_at       timestamptz NOT NULL,
    updated_at       timestamptz NOT NULL,
    version          integer NOT NULL DEFAULT 1 CHECK (version > 0),
    FOREIGN KEY (org_id, tenant_id) REFERENCES zendesk_mock.organizations (org_id, tenant_id),
    UNIQUE (ticket_id, tenant_id)
);
CREATE INDEX tickets_tenant_status ON zendesk_mock.tickets (tenant_id, status, priority);

CREATE TABLE zendesk_mock.ticket_comments (
    comment_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticket_id        text NOT NULL,
    tenant_id        text NOT NULL,
    author_kind      text NOT NULL CHECK (author_kind IN ('requester', 'zendesk_agent', 'retirementops_service')),
    public           boolean NOT NULL,
    body             text NOT NULL CHECK (length(body) BETWEEN 1 AND 20000),
    created_at       timestamptz NOT NULL,
    idempotency_key  text UNIQUE CHECK (length(idempotency_key) <= 200),
    FOREIGN KEY (ticket_id, tenant_id) REFERENCES zendesk_mock.tickets (ticket_id, tenant_id)
);

CREATE TABLE zendesk_mock.ticket_events (
    event_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticket_id    text NOT NULL,
    tenant_id    text NOT NULL,
    event_type   text NOT NULL CHECK (event_type IN ('ticket.created', 'ticket.updated',
                     'comment.added', 'assignment.changed')),
    actor_kind   text NOT NULL CHECK (actor_kind IN ('requester', 'zendesk_agent', 'retirementops_service')),
    occurred_at  timestamptz NOT NULL,
    details      jsonb NOT NULL DEFAULT '{}'::jsonb,
    FOREIGN KEY (ticket_id, tenant_id) REFERENCES zendesk_mock.tickets (ticket_id, tenant_id)
);

-- ADR-007: status may change only "inside Zendesk itself", which is simulated by the schema owner.
-- No RetirementOps runtime role can change it, even if a column grant were added by mistake.
CREATE FUNCTION zendesk_mock.guard_ticket_status() RETURNS trigger
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
CREATE TRIGGER tickets_guard_status BEFORE UPDATE ON zendesk_mock.tickets
    FOR EACH ROW EXECUTE FUNCTION zendesk_mock.guard_ticket_status();
"""

APP_SQL = r"""
-- Mock identity directory (NOT production SSO). Roles and tenant assignments are authoritative here
-- and are re-read on every request (ADR-008).
CREATE TABLE app.users (
    user_id       text PRIMARY KEY CHECK (user_id ~ '^[a-z]+\.[a-z]+$'),
    display_name  text NOT NULL,
    kind          text NOT NULL CHECK (kind IN ('human', 'service')),
    roles         text[] NOT NULL,
    tenant_ids    text[] NOT NULL,
    active        boolean NOT NULL DEFAULT true,
    CHECK (roles <@ ARRAY['operator', 'reviewer', 'compliance_reviewer', 'admin', 'agent_service']::text[])
);

-- ADR-009: append-only, hash-chained. Tamper-EVIDENT, not immutable.
CREATE TABLE app.audit_events (
    seq             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at     timestamptz NOT NULL,
    actor_principal text NOT NULL,
    actor_role      text,
    tenant_id       text,
    correlation_id  text,
    event_type      text NOT NULL CHECK (event_type ~ '^[a-z_]+(\.[a-z_]+)+$'),
    target_ref      text,
    details         jsonb NOT NULL DEFAULT '{}'::jsonb,
    prev_hash       char(64) NOT NULL,
    hash            char(64) NOT NULL UNIQUE
);

CREATE FUNCTION app.audit_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'app.audit_events is append-only (% blocked)', TG_OP
        USING ERRCODE = 'insufficient_privilege';
END
$$;
CREATE TRIGGER audit_no_update_delete BEFORE UPDATE OR DELETE ON app.audit_events
    FOR EACH ROW EXECUTE FUNCTION app.audit_append_only();
CREATE TRIGGER audit_no_truncate BEFORE TRUNCATE ON app.audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION app.audit_append_only();
"""

GRANTS_SQL = """
GRANT USAGE ON SCHEMA sec TO {all_roles};
GRANT EXECUTE ON FUNCTION sec.tenant_scope() TO {all_roles};

-- Zendesk mock
GRANT USAGE ON SCHEMA zendesk_mock TO svc_zendesk_reader, svc_zendesk_writer;
GRANT SELECT ON ALL TABLES IN SCHEMA zendesk_mock TO svc_zendesk_reader;
GRANT SELECT ON zendesk_mock.organizations, zendesk_mock.tickets, zendesk_mock.ticket_comments
    TO svc_zendesk_writer;
GRANT INSERT ON zendesk_mock.ticket_comments, zendesk_mock.ticket_events TO svc_zendesk_writer;
GRANT UPDATE (assignee_queue, updated_at, version) ON zendesk_mock.tickets TO svc_zendesk_writer;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA zendesk_mock TO svc_zendesk_writer;

-- Plan administration mock (read only)
GRANT USAGE ON SCHEMA planadmin_mock TO svc_planadmin_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA planadmin_mock TO svc_planadmin_reader;

-- Payroll mock
GRANT USAGE ON SCHEMA payroll_mock TO svc_payroll_reader, svc_payroll_ingest;
GRANT SELECT ON ALL TABLES IN SCHEMA payroll_mock TO svc_payroll_reader;
GRANT SELECT ON payroll_mock.pay_periods, payroll_mock.payroll_files, payroll_mock.payroll_rows
    TO svc_payroll_ingest;
GRANT INSERT ON payroll_mock.payroll_files, payroll_mock.payroll_rows TO svc_payroll_ingest;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA payroll_mock TO svc_payroll_ingest;

-- RetirementOps application metadata
GRANT USAGE ON SCHEMA app TO app_service;
GRANT SELECT ON app.users TO app_service;
GRANT SELECT, INSERT ON app.audit_events TO app_service;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA app TO app_service;
"""


def upgrade() -> None:
    op.execute(SCHEMAS_SQL)
    op.execute(PLANADMIN_SQL)
    op.execute(PAYROLL_SQL)
    op.execute(ZENDESK_SQL)
    op.execute(APP_SQL)
    op.execute(GRANTS_SQL.format(all_roles=", ".join(RUNTIME_ROLES)))
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (tenant_id = ANY (sec.tenant_scope())) "
            "WITH CHECK (tenant_id = ANY (sec.tenant_scope()))"
        )


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS app, zendesk_mock, payroll_mock, planadmin_mock, sec CASCADE")
