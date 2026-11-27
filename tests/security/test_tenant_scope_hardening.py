"""Migration 0005: sec.tenant_scope() pins search_path without changing behaviour or permissions.

Uses a throwaway database, so the shadowing test can create a malicious schema as superuser (runtime
roles cannot create objects anywhere, which is proven separately below).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import psycopg
import pytest
import sqlalchemy.exc
from sqlalchemy import Engine, create_engine, text

from retirementops.config import RUNTIME_ROLES, DbRole, Settings
from retirementops.db.bootstrap import create_database
from retirementops.db.migrate import downgrade_to, upgrade_head

pytestmark = pytest.mark.integration
DB = "retirementops_scope_test"
FN = "'sec.tenant_scope()'::regprocedure"
INPUTS = [None, "", " ", "TEN-AAA", "TEN-AAA,TEN-BBB", "TEN-AAA,,TEN-BBB", "TEN-AAA,"]
TENANTS = ("TEN-AAA", "TEN-BBB", "TEN-CCC")


@pytest.fixture
def scratch(settings: Settings, test_db: str) -> Iterator[Engine]:
    create_database(settings, DB, drop_existing=True)
    owner = create_engine(settings.dsn(DbRole.OWNER, DB))
    yield owner
    owner.dispose()
    with psycopg.connect(**settings.superuser_conninfo(), autocommit=True) as conn:  # type: ignore[arg-type]
        conn.execute(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)')


def _function(owner: Engine) -> dict[str, Any]:
    with owner.connect() as c:
        row = (
            c.execute(
                text(f"""
            SELECT pg_get_functiondef({FN}) AS def, l.lanname AS lang, p.provolatile::text AS vol,
                   p.proparallel::text AS par, p.prosecdef AS secdef, p.proisstrict AS strict,
                   p.proleakproof AS leakproof, format_type(p.prorettype, NULL) AS rettype,
                   p.proowner::regrole::text AS owner, p.proconfig AS config,
                   p.proacl::text AS acl
            FROM pg_proc p JOIN pg_language l ON l.oid = p.prolang WHERE p.oid = {FN}
        """)
            )
            .mappings()
            .one()
        )
    return dict(row)


def _outputs(owner: Engine) -> list[Any]:
    out = []
    with owner.connect() as c:
        for value in INPUTS:
            with c.begin():
                if value is not None:
                    c.execute(
                        text("SELECT set_config('retirementops.tenant_ids', :v, true)"),
                        {"v": value},
                    )
                out.append(c.execute(text("SELECT sec.tenant_scope()")).scalar())
    return out


def _visible_tenants(
    settings: Settings, scope: str | None, search_path: str | None = None
) -> list[str]:
    reader = create_engine(settings.dsn(DbRole.PLANADMIN_READER, DB))
    try:
        with reader.begin() as c:
            if search_path:
                c.execute(text(f"SET LOCAL search_path = {search_path}"))
            if scope is not None:
                c.execute(
                    text("SELECT set_config('retirementops.tenant_ids', :v, true)"), {"v": scope}
                )
            return sorted(
                c.execute(text("SELECT tenant_id FROM planadmin_mock.tenants")).scalars().all()
            )
    finally:
        reader.dispose()


def _seed_tenants(owner: Engine) -> None:
    with owner.begin() as c:
        for t in TENANTS:
            c.execute(
                text("INSERT INTO planadmin_mock.tenants VALUES (:t, 'x (synthetic)', 1, now())"),
                {"t": t},
            )


# ---------------------------------------------------------------- pinned, behaviour preserved
def test_upgrade_pins_search_path_and_preserves_everything_else(scratch: Engine) -> None:
    upgrade_head(DB)
    at_head = _function(scratch)
    downgrade_to("0004", DB)
    at_0004, out_0004 = _function(scratch), _outputs(scratch)
    upgrade_head(DB)
    downgrade_to("0005", DB)  # the exact 0005 state, before later migrations add grants
    at_0005, out_0005 = _function(scratch), _outputs(scratch)

    # After 0005, only 0007 touches the function: EXECUTE for svc_agent_recorder (RLS on traces).
    recorder = f"{DbRole.AGENT_RECORDER.value}=X/ro_owner"
    assert {k: v for k, v in at_head.items() if k != "acl"} == {
        k: v for k, v in at_0005.items() if k != "acl"
    }
    assert recorder in at_head["acl"] and recorder not in at_0005["acl"]
    assert at_0004["config"] is None
    assert at_0005["config"] == ["search_path=pg_catalog"]
    for attr in ("lang", "vol", "par", "secdef", "strict", "leakproof", "rettype", "owner", "acl"):
        assert at_0005[attr] == at_0004[attr], attr
    assert (at_0005["lang"], at_0005["vol"], at_0005["par"]) == ("sql", "s", "s")
    assert at_0005["secdef"] is False and at_0005["rettype"] == "text[]"
    assert at_0005["owner"] == "ro_owner"
    pre_m3 = [r.value for r in RUNTIME_ROLES if r is not DbRole.AGENT_RECORDER]
    for role in ("ro_owner", *pre_m3):  # EXECUTE grants unchanged by 0005
        assert f"{role}=X/ro_owner" in at_0005["acl"]
    assert out_0005 == out_0004  # identical results for every input
    assert out_0005 == [
        [],
        [],
        [" "],
        ["TEN-AAA"],
        ["TEN-AAA", "TEN-BBB"],
        ["TEN-AAA", "", "TEN-BBB"],
        ["TEN-AAA", ""],
    ]


def test_downgrade_restores_the_exact_previous_definition(scratch: Engine) -> None:
    upgrade_head(DB)
    downgrade_to("0004", DB)
    original = _function(scratch)
    upgrade_head(DB)
    downgrade_to("0004", DB)
    restored = _function(scratch)
    assert restored == original  # definition text, attributes, owner, ACL, no config
    assert restored["config"] is None


def test_rls_policies_still_reference_the_function_and_isolate_tenants(
    scratch: Engine, settings: Settings
) -> None:
    upgrade_head(DB)
    _seed_tenants(scratch)
    with scratch.connect() as c:
        policies = c.execute(
            text(
                "SELECT count(*) FROM pg_policies WHERE qual LIKE '%tenant_scope()%' "
                "AND with_check LIKE '%tenant_scope()%'"
            )
        ).scalar_one()
    assert policies == 17  # 14 through M2, plus the three run-trace tables (0007)
    assert _visible_tenants(settings, None) == []
    assert _visible_tenants(settings, "") == []
    assert _visible_tenants(settings, "TEN-AAA") == ["TEN-AAA"]
    assert _visible_tenants(settings, "TEN-AAA,TEN-CCC") == ["TEN-AAA", "TEN-CCC"]


# ---------------------------------------------------------------- shadowing
SHADOW = """
CREATE SCHEMA evil;
CREATE FUNCTION evil.string_to_array(text, text) RETURNS text[] LANGUAGE sql
    AS $$ SELECT ARRAY['TEN-AAA', 'TEN-BBB', 'TEN-CCC'] $$;
CREATE FUNCTION evil.current_setting(text, boolean) RETURNS text LANGUAGE sql
    AS $$ SELECT 'TEN-AAA,TEN-BBB,TEN-CCC' $$;
GRANT USAGE ON SCHEMA evil TO svc_planadmin_reader;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA evil TO svc_planadmin_reader;
"""


def test_search_path_shadowing_widens_scope_at_0004_but_not_at_0005(
    scratch: Engine, settings: Settings
) -> None:
    """Simulates a role that COULD create objects (which no runtime role can; see below)."""
    upgrade_head(DB)
    downgrade_to("0004", DB)
    _seed_tenants(scratch)
    with psycopg.connect(**settings.superuser_conninfo(DB), autocommit=True) as su:  # type: ignore[arg-type]
        su.execute(SHADOW)
    attack = "evil, pg_catalog"
    # Baseline: the exploit is real against the unpinned function (the test is meaningful).
    assert _visible_tenants(settings, "TEN-AAA", attack) == list(TENANTS)
    upgrade_head(DB)
    assert _visible_tenants(settings, "TEN-AAA", attack) == ["TEN-AAA"]
    assert _visible_tenants(settings, None, attack) == []  # fail-closed default still holds


@pytest.mark.parametrize("role", list(RUNTIME_ROLES), ids=lambda r: r.value)
@pytest.mark.parametrize(
    "stmt",
    [
        "CREATE SCHEMA shadow",
        "CREATE TEMP TABLE shadow (x int)",
        "CREATE FUNCTION public.string_to_array(text, text) RETURNS text[] LANGUAGE sql "
        "AS $$ SELECT ARRAY['TEN-ALDER'] $$",
        "CREATE FUNCTION sec.string_to_array(text, text) RETURNS text[] LANGUAGE sql "
        "AS $$ SELECT ARRAY['TEN-ALDER'] $$",
    ],
)
def test_runtime_roles_cannot_create_shadow_objects(
    role_engine: Callable[[DbRole], Engine], role: DbRole, stmt: str
) -> None:
    with (
        pytest.raises(sqlalchemy.exc.ProgrammingError, match="permission denied"),
        role_engine(role).begin() as conn,
    ):
        conn.execute(text(stmt))


def test_head_function_is_pinned_in_the_shared_test_database(owner_engine: Engine) -> None:
    with owner_engine.connect() as c:
        config = c.execute(text(f"SELECT proconfig FROM pg_proc WHERE oid = {FN}")).scalar()
    assert config == ["search_path=pg_catalog"]
