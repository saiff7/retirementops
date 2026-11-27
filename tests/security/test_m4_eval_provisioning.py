"""M4 Stage 2: the evaluation database, the scoring reader and the settings separation.

ADR-010 Amendment 1, points 1 and 1a. Each test checks the live catalog or a real connection; the
dev database is never connected to (only the shared catalog is read).
"""

from __future__ import annotations

import ast
import time
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError

from retirementops.config import (
    NON_RUNTIME_ROLES,
    RUNTIME_ROLES,
    DbRole,
    MissingSecret,
    Settings,
)
from retirementops.db.bootstrap import ensure_roles
from retirementops.db.engines import make_engine, make_runtime_engines
from retirementops.db.scoped import scoped_tx
from retirementops.domain.authz import TenantScope
from retirementops.evaluations import provision as prov
from retirementops.evaluations.dataset import load as load_golden

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
PROJECT_SCHEMAS = ("app", "sec", "zendesk_mock", "planadmin_mock", "payroll_mock", "servicekb_mock")
TRIGGER_FUNCTIONS = {
    "app.audit_append_only",
    "app.guard_draft_insert",
    "app.guard_draft_version_insert",
    "app.guard_proposal_insert",
    "app.guard_trace_insert",
    "app.immutable_row",
    "zendesk_mock.guard_ticket_status",
}


def _su(settings: Settings, db: str = "postgres") -> psycopg.Connection:
    return psycopg.connect(**settings.superuser_conninfo(db), autocommit=True)  # type: ignore[arg-type]


def _db_oids(settings: Settings) -> dict[str, int]:
    with _su(settings) as c:
        return {n: o for n, o in c.execute("SELECT datname, oid::int FROM pg_database").fetchall()}


# ---------------------------------------------------------------- name guard and rebuild
@pytest.mark.parametrize(
    "name",
    [
        "retirementops",
        "retirementops_test",
        "postgres",
        "template1",
        "retirementops_eval2",
        "RETIREMENTOPS_EVAL",
        " retirementops_eval",
        'retirementops_eval"; DROP DATABASE retirementops; --',
    ],
)
def test_any_other_database_name_is_refused(settings: Settings, name: str) -> None:
    before = _db_oids(settings)
    with pytest.raises(prov.ProvisionError):
        prov.provision(settings, name)
    with pytest.raises(prov.ProvisionError):
        prov.cluster_step(settings, name)
    with pytest.raises(prov.ProvisionError):
        prov.database_step(settings, name)
    assert _db_oids(settings) == before  # nothing was created, dropped or recreated


def test_cli_refuses_the_dev_database(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert prov.main(["--db", "retirementops"]) == 2
    assert "provision_refused" in capsys.readouterr().err


def test_rebuild_is_idempotent_fast_and_leaves_other_databases_alone(
    settings: Settings, eval_db: str
) -> None:
    before = _db_oids(settings)
    first = prov.provision(settings, eval_db)
    second = prov.provision(settings, eval_db)
    after = _db_oids(settings)
    assert (
        first.seed_dataset_hash
        == second.seed_dataset_hash
        == load_golden().manifest.seed_dataset_hash
    )
    assert second.seconds < 60
    for other in ("retirementops", "retirementops_test", "postgres"):
        if other in before:
            assert after[other] == before[other], other  # never dropped or recreated
    assert after[eval_db] != before[eval_db]  # the eval database itself was rebuilt


def test_tampered_seed_row_is_detected(settings: Settings, eval_db: str) -> None:
    prov.provision(settings, eval_db)
    owner = create_engine(settings.dsn(DbRole.OWNER, eval_db))
    try:
        with owner.begin() as c:
            c.execute(
                text(
                    "UPDATE planadmin_mock.plans SET name = name || ' x' WHERE plan_id = 'PLAN-001'"
                )
            )
        with pytest.raises(prov.ProvisionError, match="expected seed data"):
            prov.verify_seed_hash(owner, load_golden().manifest.seed_dataset_hash)
    finally:
        owner.dispose()
        prov.provision(settings, eval_db)  # leave a clean database for later tests


# ---------------------------------------------------------------- the reader role
def test_reader_role_attributes_and_no_memberships(settings: Settings, eval_db: str) -> None:
    with _su(settings) as c:
        row = c.execute(
            "SELECT rolsuper, rolinherit, rolcreaterole, rolcreatedb, rolreplication, "
            "rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname = %s",
            (prov.READER,),
        ).fetchone()
        members = c.execute(
            "SELECT count(*) FROM pg_auth_members a JOIN pg_roles r ON r.oid IN (a.member, a.roleid) "
            "WHERE r.rolname = %s",
            (prov.READER,),
        ).fetchone()
    assert row == (False, False, False, False, False, False, True)
    assert members == (0,)


def test_reader_connect_rights_per_database(settings: Settings, eval_db: str) -> None:
    with _su(settings) as c:
        names = {r[0] for r in c.execute("SELECT datname FROM pg_database").fetchall()}

        def can(db: str) -> bool:
            return bool(
                c.execute(
                    "SELECT has_database_privilege(%s, %s, 'CONNECT')", (prov.READER, db)
                ).fetchone()[0]  # type: ignore[index]
            )

        assert can(eval_db)
        assert not can("retirementops_test")
        if "retirementops" in names:  # CI has no dev database: absent means no access
            assert not can("retirementops")
        for priv in ("CREATE", "TEMPORARY"):
            assert not c.execute(
                "SELECT has_database_privilege(%s, %s, %s)", (prov.READER, eval_db, priv)
            ).fetchone()[0]  # type: ignore[index]


def test_reader_cannot_connect_to_the_test_database(
    settings: Settings, test_db: str, eval_db: str
) -> None:
    engine = make_engine(settings, DbRole.EVAL_READER, test_db)
    try:
        with pytest.raises(DBAPIError, match="permission denied"), engine.connect():
            pass
    finally:
        engine.dispose()


def test_eval_database_acl_lists_exactly_the_approved_roles(
    settings: Settings, eval_db: str
) -> None:
    with _su(settings) as c:
        rows = c.execute(
            "SELECT a.grantee, coalesce(r.rolname, 'PUBLIC'), a.privilege_type FROM pg_database d, "
            "aclexplode(d.datacl) a LEFT JOIN pg_roles r ON r.oid = a.grantee WHERE d.datname = %s",
            (eval_db,),
        ).fetchall()
    grantees = {name for _, name, _ in rows}
    assert "PUBLIC" not in grantees and all(g != 0 for g, _, _ in rows)
    assert grantees == {"ro_owner", prov.READER, *(r.value for r in RUNTIME_ROLES)}
    reader_privs = {p for _, n, p in rows if n == prov.READER}
    assert reader_privs == {"CONNECT"}


def test_reader_privileges_equal_the_allowlist_exactly(settings: Settings, eval_db: str) -> None:
    with _su(settings, eval_db) as c:
        tables = c.execute(
            "SELECT n.nspname || '.' || c.relname, p.priv, "
            "has_table_privilege(%s, c.oid, p.priv) FROM pg_class c JOIN pg_namespace n "
            "ON n.oid = c.relnamespace CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE',"
            "'TRUNCATE','REFERENCES','TRIGGER']) p(priv) WHERE c.relkind IN ('r','p','v','m','f') "
            "AND n.nspname = ANY(%s)",
            (prov.READER, [*list(PROJECT_SCHEMAS), "public"]),
        ).fetchall()
        granted = {(t, p) for t, p, ok in tables if ok}
        assert granted == {(t, "SELECT") for t in prov.READER_TABLES}

        seqs = c.execute(
            "SELECT c.relname FROM pg_class c WHERE c.relkind = 'S' AND (has_sequence_privilege(%s, "
            "c.oid, 'USAGE') OR has_sequence_privilege(%s, c.oid, 'SELECT') OR "
            "has_sequence_privilege(%s, c.oid, 'UPDATE'))",
            (prov.READER,) * 3,
        ).fetchall()
        assert seqs == []

        schemas = c.execute(
            "SELECT nspname, has_schema_privilege(%s, oid, 'USAGE'), has_schema_privilege(%s, oid, "
            "'CREATE') FROM pg_namespace WHERE nspname = ANY(%s)",
            (prov.READER, prov.READER, [*list(PROJECT_SCHEMAS), "public"]),
        ).fetchall()
        assert {n for n, usage, _ in schemas if usage} == set(prov.READER_SCHEMAS)
        assert not any(create for _, _, create in schemas)

        funcs = c.execute(
            "SELECT n.nspname || '.' || p.proname, p.prorettype = 'trigger'::regtype FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = ANY(%s) AND "
            "has_function_privilege(%s, p.oid, 'EXECUTE')",
            (list(PROJECT_SCHEMAS), prov.READER),
        ).fetchall()
        executable = {f for f, _ in funcs}
        assert executable == {"sec.tenant_scope"} | TRIGGER_FUNCTIONS
        assert {f for f, is_trigger in funcs if not is_trigger} == {"sec.tenant_scope"}


def test_reader_session_defaults_to_read_only(eval_reader_engine: Engine) -> None:
    with eval_reader_engine.connect() as c:
        assert c.execute(text("SHOW default_transaction_read_only")).scalar_one() == "on"


@pytest.mark.parametrize("qualified", prov.READER_TABLES)
def test_reader_cannot_write_any_table_it_can_read(
    settings: Settings, eval_db: str, qualified: str
) -> None:
    """Even with the read-only default switched off, the grants alone deny every write."""
    schema, name = qualified.split(".")
    engine = make_engine(settings, DbRole.EVAL_READER, eval_db)
    try:
        with engine.connect() as c:
            col = c.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema = :s "
                    "AND table_name = :t AND is_identity = 'NO' ORDER BY ordinal_position LIMIT 1"
                ),
                {"s": schema, "t": name},
            ).scalar_one()
        statements = [
            f"INSERT INTO {qualified} DEFAULT VALUES",
            f"UPDATE {qualified} SET {col} = {col}",
            f"DELETE FROM {qualified}",
            f"TRUNCATE {qualified}",
        ]
        for stmt in statements:
            with engine.connect() as c:
                c.execute(text("SET default_transaction_read_only = off"))
                c.execute(text("SET transaction_read_only = off"))
                with pytest.raises(DBAPIError, match="permission denied"):
                    c.execute(text(stmt))
    finally:
        engine.dispose()


def test_reader_cannot_create_objects_or_use_sequences(settings: Settings, eval_db: str) -> None:
    engine = make_engine(settings, DbRole.EVAL_READER, eval_db)
    try:
        for stmt in (
            "CREATE TEMP TABLE t (x int)",
            "CREATE TABLE app.t (x int)",
            "CREATE TABLE public.t (x int)",
            "SELECT nextval('zendesk_mock.ticket_events_event_id_seq')",
        ):
            with engine.connect() as c:
                c.execute(text("SET default_transaction_read_only = off"))
                c.execute(text("SET transaction_read_only = off"))
                with pytest.raises(DBAPIError, match="permission denied"):
                    c.execute(text(stmt))
    finally:
        engine.dispose()


@pytest.mark.parametrize("qualified", ["app.audit_events", "app.users", "public.alembic_version"])
def test_reader_cannot_read_excluded_tables(eval_reader_engine: Engine, qualified: str) -> None:
    with eval_reader_engine.connect() as c, pytest.raises(DBAPIError, match="permission denied"):
        c.execute(text(f"SELECT count(*) FROM {qualified}"))


def test_rls_still_applies_to_the_reader(eval_reader_engine: Engine) -> None:
    with eval_reader_engine.connect() as c:  # no tenant scope: tenant tables look empty
        assert c.execute(text("SELECT count(*) FROM zendesk_mock.tickets")).scalar_one() == 0
        assert c.execute(text("SELECT count(*) FROM planadmin_mock.tenants")).scalar_one() == 0
        assert c.execute(text("SELECT count(*) FROM payroll_mock.pay_periods")).scalar_one() == 8
    with scoped_tx(eval_reader_engine, TenantScope(frozenset({"TEN-ALDER"}))) as c:
        tenants = (
            c.execute(text("SELECT DISTINCT tenant_id FROM zendesk_mock.tickets")).scalars().all()
        )
        assert tenants == ["TEN-ALDER"]


# ---------------------------------------------------------------- runtime separation
def test_reader_is_not_a_runtime_role(settings: Settings) -> None:
    assert DbRole.EVAL_READER not in RUNTIME_ROLES and DbRole.OWNER not in RUNTIME_ROLES
    assert {DbRole.OWNER, DbRole.EVAL_READER} == NON_RUNTIME_ROLES
    engines = make_runtime_engines(settings, "retirementops_eval")
    try:
        assert DbRole.EVAL_READER not in engines and DbRole.OWNER not in engines
    finally:
        for e in engines.values():
            e.dispose()


# ---------------------------------------------------------------- settings mode (OPEN-11, eval only)
def test_unprivileged_settings_hold_no_privileged_secret() -> None:
    s = Settings.unprivileged()
    assert s.postgres_superuser_password is None and s.ro_pw_ro_owner is None
    assert not s.is_privileged
    with pytest.raises(MissingSecret):
        s.role_password(DbRole.OWNER)
    with pytest.raises(MissingSecret):
        s.dsn(DbRole.OWNER)
    with pytest.raises(MissingSecret):
        s.superuser_conninfo()
    with pytest.raises(MissingSecret):
        make_engine(s, DbRole.OWNER, "retirementops_eval")
    # The roles the system under test and the reader need are still available.
    for role in (*RUNTIME_ROLES, DbRole.EVAL_READER):
        assert s.role_password(role).get_secret_value()
    secrets = {v.get_secret_value() for v in s.__dict__.values() if hasattr(v, "get_secret_value")}
    full = Settings()
    assert full.is_privileged
    assert full.ro_pw_ro_owner is not None and full.postgres_superuser_password is not None
    assert full.ro_pw_ro_owner.get_secret_value() not in secrets
    assert full.postgres_superuser_password.get_secret_value() not in secrets


def test_privileged_entry_points_stop_clearly_without_the_secrets() -> None:
    s = Settings.unprivileged()
    with pytest.raises(MissingSecret, match="superuser"):
        ensure_roles(s)
    with pytest.raises(MissingSecret):
        prov.provision(s, prov.EVAL_DB)
    with pytest.raises(prov.ProvisionError):  # the name guard still runs first
        prov.provision(s, "retirementops")


def test_only_provisioning_refers_to_privileged_identities() -> None:
    """Outside existing bootstrap, migration, seed and test code, only evaluations.provision may
    name the owner role or open a superuser connection."""
    allowed = {
        "src/retirementops/config.py",
        "src/retirementops/db/bootstrap.py",
        "src/retirementops/evaluations/provision.py",
    }
    offenders = []
    for path in sorted((ROOT / "src/retirementops").rglob("*.py")):
        rel = str(path.relative_to(ROOT))
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            hit = (
                isinstance(node, ast.Attribute) and node.attr in {"OWNER", "superuser_conninfo"}
            ) or (isinstance(node, ast.Constant) and node.value == "ro_owner")
            if hit and rel not in allowed:
                offenders.append(rel)
    assert offenders == []


def test_provisioning_took_under_a_minute(settings: Settings, eval_db: str) -> None:
    started = time.monotonic()
    prov.provision(settings, eval_db)
    assert time.monotonic() - started < 60
