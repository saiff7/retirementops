"""Regression tests for the independent M2 review findings (H-1, M-1, M-2, Low-4/7/8/9).

Each test names its finding. DB probes run in transactions that are rolled back.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import anyio
import pytest
import sqlalchemy.exc
from mcp.client.client import Client
from sqlalchemy import Connection, Engine, text

from retirementops.config import DbRole
from retirementops.governance.tokens import TokenService
from retirementops.mcp_servers.service_ops.http import allowed_hosts
from retirementops.mcp_servers.service_ops.server import ServiceOpsMcp, TokenPrincipalSource
from retirementops.seed.generator import Dataset
from retirementops.services.container import Container
from tests.conftest import McpCall

pytestmark = pytest.mark.integration
RoleEngine = Callable[[DbRole], Engine]
SKILL = "service-ticket-triage@0.1.0"
H = "0" * 64


def _ref(source: dict[str, str]) -> dict[str, str]:
    return {k: source[k] for k in ("system", "record_type", "record_id", "content_hash")}


@pytest.fixture
def app_tx(role_engine: RoleEngine) -> Iterator[Connection]:
    """A rolled-back transaction as app_service, scoped to Alder."""
    conn = role_engine(DbRole.APP_SERVICE).connect()
    tx = conn.begin()
    conn.execute(text("SELECT set_config('retirementops.tenant_ids', 'TEN-ALDER', true)"))
    try:
        yield conn
    finally:
        tx.rollback()
        conn.close()


def _insert_draft(
    conn: Connection,
    draft_id: str,
    status: str,
    created_by: str = "op.alex",
    created_by_kind: str = "human",
) -> None:
    conn.execute(
        text(
            "INSERT INTO app.drafts (draft_id, ticket_id, tenant_id, status, status_reasons, created_by,"
            " created_at, created_by_kind) VALUES (:d, 'ZD-1001', 'TEN-ALDER', :s, '[]', :c, "
            "'2026-05-01T12:00:00+00:00', :k)"
        ),
        {"d": draft_id, "s": status, "c": created_by, "k": created_by_kind},
    )


def _insert_version(
    conn: Connection,
    draft_id: str,
    *,
    version: int = 1,
    author: str = "op.alex",
    created_at: str = "2026-05-01T12:00:00+00:00",
    author_kind: str = "human",
) -> None:
    conn.execute(
        text(
            "INSERT INTO app.draft_versions VALUES (:d, 'TEN-ALDER', :v, 'body', '[]', 'a@1.0.0', 1, :h,"
            " :a, :k, :t)"
        ),
        {"d": draft_id, "v": version, "h": H, "a": author, "t": created_at, "k": author_kind},
    )


# ---------------------------------------------------------------- H-1
@pytest.mark.parametrize(
    "status",
    # PENDING_REVIEW was refused in M2 (final review Low-2). M3 (0006) re-allows it only with a
    # passing validator; tests/security/test_m3_draft_validation.py covers that case.
    ["APPROVED", "REJECTED", "ESCALATED", "EXECUTED", "EXECUTION_FAILED"],
)
def test_h1_app_role_cannot_create_draft_in_a_later_state(app_tx: Connection, status: str) -> None:
    with pytest.raises(sqlalchemy.exc.DBAPIError, match="can only be created as"):
        _insert_draft(app_tx, "DRF-00000000000000A1", status)


@pytest.mark.parametrize("kind", ["human", "service"])
def test_h1_initial_state_and_matching_v1_are_allowed(app_tx: Connection, kind: str) -> None:
    _insert_draft(app_tx, "DRF-00000000000000A2", "NEEDS_MANUAL_REVIEW", created_by_kind=kind)
    _insert_version(app_tx, "DRF-00000000000000A2", author_kind=kind)  # same creator, kind, time


@pytest.mark.parametrize("status", ["APPROVED", "REJECTED", "EXECUTED"])
def test_h1_app_role_cannot_create_proposal_in_a_later_state(
    app_tx: Connection, status: str
) -> None:
    with pytest.raises(sqlalchemy.exc.DBAPIError, match="can only be created as PROPOSED"):
        app_tx.execute(
            text(
                "INSERT INTO app.action_proposals VALUES ('PRP-00000000000000A1', 'TEN-ALDER', 'ZD-1001',"
                " 'ASSIGN_QUEUE', 'HUMAN_TRIAGE', 'x', :s, 1, :h, 'op.alex', now())"
            ),
            {"s": status, "h": H},
        )


def test_h1_app_role_cannot_append_later_versions(app_tx: Connection, mcp_call: McpCall) -> None:
    ticket = _ref(
        mcp_call("op.alex", "get_ticket_details", {"ticket_id": "ZD-1001"})["data"]["source"]
    )
    draft = mcp_call(
        "op.alex",
        "create_reply_draft",
        {
            "ticket_id": "ZD-1001",
            "body": "v1",
            "evidence_refs": [ticket],
            "skill_version": SKILL,
        },
    )["data"]
    with pytest.raises(sqlalchemy.exc.DBAPIError, match="only version 1"):
        _insert_version(app_tx, draft["draft_id"], version=2, author="rev.lee")


@pytest.mark.parametrize(
    ("author", "created_at", "kind"),
    [
        ("rev.lee", "2026-05-01T12:00:00+00:00", "service"),  # forged author
        ("op.alex", "2026-05-02T09:00:00+00:00", "service"),  # written apart from the header
        ("op.alex", "2026-05-01T12:00:00+00:00", "human"),  # agent's v1 relabelled as human
    ],
)
def test_h1_v1_must_match_its_header(
    app_tx: Connection, author: str, created_at: str, kind: str
) -> None:
    _insert_draft(
        app_tx,
        "DRF-00000000000000A3",
        "NEEDS_MANUAL_REVIEW",
        created_by="op.alex",
        created_by_kind="service",
    )
    with pytest.raises(sqlalchemy.exc.DBAPIError, match="must match its draft header"):
        _insert_version(
            app_tx, "DRF-00000000000000A3", author=author, created_at=created_at, author_kind=kind
        )


# ---------------------------------------------------------------- M-1
def _call_as(container: Container, token: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    server = ServiceOpsMcp(container, TokenPrincipalSource(container, token))

    async def go() -> dict[str, Any]:
        async with Client(server.server) as client:
            return dict((await client.call_tool(tool, args)).structured_content or {})

    return anyio.run(go)


def test_m1_evidence_cannot_probe_data_outside_caller_scopes(
    container: Container, token_service: TokenService, mcp_call: McpCall
) -> None:
    narrow = token_service.issue("op.alex", frozenset({"tickets:read", "drafts:write"}))
    ticket = _ref(
        mcp_call("op.alex", "get_ticket_details", {"ticket_id": "ZD-1001"})["data"]["source"]
    )
    row = mcp_call(
        "op.alex",
        "get_payroll_contributions",
        {"participant_ref": "PART-0002", "plan_id": "PLAN-001", "pay_period": "PP-2026-03"},
    )["data"]["rows"][0]["source"]

    def message(
        token: str, record_id: str, record_type: str = "payroll_row", system: str = "payroll_mock"
    ) -> str:
        bad = {
            "system": system,
            "record_type": record_type,
            "record_id": record_id,
            "content_hash": H,
        }
        r = _call_as(
            container,
            token,
            "create_reply_draft",
            {
                "ticket_id": "ZD-1001",
                "body": "probe",
                "skill_version": SKILL,
                "evidence_refs": [ticket, bad],
            },
        )
        return str(r["message"])

    assert (
        _call_as(
            container,
            narrow,
            "get_payroll_contributions",
            {"participant_ref": "PART-0002", "plan_id": "PLAN-001", "pay_period": "PP-2026-03"},
        )["error"]
        == "forbidden"
    )
    existing, missing = message(narrow, row["record_id"]), message(narrow, "PF-000000000000#1")
    assert existing == missing  # no existence oracle without payroll:read
    assert "not readable by this caller" in existing
    assert message(narrow, "PLAN-001", "plan", "planadmin_mock") == existing.replace(
        "evidence[1]", "evidence[1]"
    )  # plans:read is enforced the same way
    full = token_service.issue(
        "op.alex", frozenset({"tickets:read", "drafts:write", "payroll:read"})
    )
    assert "hash does not match" in message(full, row["record_id"])  # scope is the only difference


# ---------------------------------------------------------------- M-2
def test_m2_unknown_tool_names_do_not_grow_metrics(
    container: Container, bearer: Callable[[str], dict[str, str]]
) -> None:
    server = ServiceOpsMcp(
        container, TokenPrincipalSource(container, bearer("op.alex")["Authorization"].split()[1])
    )

    async def go() -> None:
        async with Client(server.server) as client:
            for i in range(60):
                await client.call_tool(f"probe_tool_{i:03d}", {})

    anyio.run(go)
    snap = server.metrics.snapshot()
    assert set(snap) == {"<unknown>"}
    assert snap["<unknown>"]["calls"] == 60


# ---------------------------------------------------------------- Low-4
def test_low4_ipv6_loopback_host_is_allowed() -> None:
    assert "[::1]:8010" in allowed_hosts(8010)


# ---------------------------------------------------------------- Low-7
def test_low7_draft_must_cite_the_ticket_it_answers(
    mcp_call: McpCall, owner_engine: Engine
) -> None:
    plan = _ref(mcp_call("rev.sam", "get_plan_details", {"plan_id": "PLAN-001"})["data"]["source"])
    other = _ref(
        mcp_call("rev.sam", "get_ticket_details", {"ticket_id": "ZD-1002"})["data"]["source"]
    )
    with owner_engine.connect() as conn:
        before = conn.execute(text("SELECT count(*) FROM app.drafts")).scalar_one()
    for refs in ([plan], [other]):  # another ticket's evidence does not count
        r = mcp_call(
            "rev.sam",
            "create_reply_draft",
            {"ticket_id": "ZD-1001", "body": "x", "evidence_refs": refs, "skill_version": SKILL},
        )
        assert r["data"]["error"] == "invalid_request"
        assert "must include the ticket being answered" in r["data"]["message"]
    assert (
        mcp_call(
            "rev.sam",
            "create_reply_draft",
            {
                "ticket_id": "ZD-1001",
                "body": "x",
                "evidence_refs": [],
                "skill_version": SKILL,
            },
        )["data"]["error"]
        == "invalid_arguments"
    )
    with owner_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM app.drafts")).scalar_one() == before


# ---------------------------------------------------------------- Low-8
def test_low8_idempotent_draft_creation(
    mcp_call: McpCall, owner_engine: Engine, dataset: Dataset
) -> None:
    ticket = _ref(
        mcp_call("op.alex", "get_ticket_details", {"ticket_id": "ZD-1001"})["data"]["source"]
    )
    args = {
        "ticket_id": "ZD-1001",
        "body": "Reviewing now.",
        "evidence_refs": [ticket],
        "skill_version": SKILL,
        "idempotency_key": "run-123:draft-1",
    }
    first = mcp_call("op.alex", "create_reply_draft", args)["data"]
    again = mcp_call("op.alex", "create_reply_draft", args)["data"]
    assert not first["replayed"] and again["replayed"]
    assert again["draft_id"] == first["draft_id"] and again["content_hash"] == first["content_hash"]
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT count(*) FROM app.drafts WHERE idempotency_key = 'run-123:draft-1'")
        ).scalar_one()
        audits = conn.execute(
            text(
                "SELECT count(*) FROM app.audit_events WHERE event_type = "
                "'draft.created' AND target_ref = :d"
            ),
            {"d": first["draft_id"]},
        ).scalar_one()
    assert rows == 1 and audits == 1
    changed = mcp_call("op.alex", "create_reply_draft", {**args, "body": "Different text."})
    assert changed["data"]["error"] == "invalid_request"
    assert "different draft" in changed["data"]["message"]
    bad_key = mcp_call("op.alex", "create_reply_draft", {**args, "idempotency_key": "short"})
    assert bad_key["data"]["error"] == "invalid_arguments"


def test_low8_idempotency_keys_are_per_tenant(mcp_call: McpCall, dataset: Dataset) -> None:
    birch = next(
        t["ticket_id"]
        for t in dataset.tables["zendesk_mock.tickets"]
        if t["tenant_id"] == "TEN-BIRCH" and t["status"] == "new"
    )
    key = "shared-key-across-tenants"
    a = mcp_call(
        "rev.sam",
        "create_reply_draft",
        {
            "ticket_id": "ZD-1001",
            "body": "x",
            "skill_version": SKILL,
            "idempotency_key": key,
            "evidence_refs": [
                _ref(
                    mcp_call("rev.sam", "get_ticket_details", {"ticket_id": "ZD-1001"})["data"][
                        "source"
                    ]
                )
            ],
        },
    )["data"]
    b = mcp_call(
        "rev.sam",
        "create_reply_draft",
        {
            "ticket_id": birch,
            "body": "x",
            "skill_version": SKILL,
            "idempotency_key": key,
            "evidence_refs": [
                _ref(
                    mcp_call("rev.sam", "get_ticket_details", {"ticket_id": birch})["data"][
                        "source"
                    ]
                )
            ],
        },
    )["data"]
    assert a["draft_id"] != b["draft_id"] and not b["replayed"]


# ---------------------------------------------------------------- Low-9
def test_low9_bare_nine_digit_identifiers_rejected_in_drafts(mcp_call: McpCall) -> None:
    ticket = _ref(
        mcp_call("op.alex", "get_ticket_details", {"ticket_id": "ZD-1001"})["data"]["source"]
    )
    r = mcp_call(
        "op.alex",
        "create_reply_draft",
        {
            "ticket_id": "ZD-1001",
            "body": "Employee 123456789 is enrolled.",
            "skill_version": SKILL,
            "evidence_refs": [ticket],
        },
    )
    assert r["data"]["error"] == "invalid_request" and "123456789" not in r["text"]
    r = mcp_call(
        "op.alex",
        "propose_ticket_assignment",
        {"ticket_id": "ZD-1001", "target_queue": "HUMAN_TRIAGE", "rationale": "TIN 987654321"},
    )
    assert r["data"]["error"] == "invalid_request"


def test_low9_search_results_mark_subject_untrusted(mcp_call: McpCall) -> None:
    items = mcp_call("op.alex", "search_tickets", {"page_size": 5})["data"]["items"]
    assert items and all(i["subject"]["trust"] == "UNTRUSTED_REQUESTER_TEXT" for i in items)


# ---------------------------------------------------------------- N-1 (final review)
def _n1_setup(mcp_call: McpCall) -> tuple[dict[str, str], dict[str, str]]:
    ticket = _ref(
        mcp_call("op.alex", "get_ticket_details", {"ticket_id": "ZD-1001"})["data"]["source"]
    )
    row = _ref(
        mcp_call(
            "op.alex",
            "get_payroll_contributions",
            {
                "participant_ref": "PART-0002",
                "plan_id": "PLAN-001",
                "pay_period": "PP-2026-03",
            },
        )["data"]["rows"][0]["source"]
    )
    return ticket, row


def _draft_args(
    key: str | None, refs: list[dict[str, str]], body: str = "Reviewing now.", skill: str = SKILL
) -> dict[str, Any]:
    args: dict[str, Any] = {
        "ticket_id": "ZD-1001",
        "body": body,
        "evidence_refs": refs,
        "skill_version": skill,
    }
    if key is not None:
        args["idempotency_key"] = key
    return args


def test_n1_user_without_payroll_read_cannot_replay_payroll_evidence(
    container: Container, token_service: TokenService, mcp_call: McpCall
) -> None:
    """Mandatory 3 and 5a: the scope check runs before any replay lookup."""
    ticket, row = _n1_setup(mcp_call)
    key = "n1-scope-key-0001"
    orig = mcp_call("op.alex", "create_reply_draft", _draft_args(key, [ticket, row]))["data"]
    assert not orig["replayed"]
    narrow = token_service.issue("op.alex", frozenset({"tickets:read", "drafts:write"}))
    replay = _call_as(container, narrow, "create_reply_draft", _draft_args(key, [ticket, row]))
    fresh = _call_as(container, narrow, "create_reply_draft", _draft_args(None, [ticket, row]))
    unused = _call_as(
        container, narrow, "create_reply_draft", _draft_args("n1-never-used-key", [ticket, row])
    )
    assert (
        replay["error"] == "invalid_request" and "not readable by this caller" in replay["message"]
    )
    assert replay == fresh == unused  # identical whether or not a matching draft exists
    assert "draft_id" not in replay


def test_n1_different_user_never_matches_another_users_key(
    mcp_call: McpCall, owner_engine: Engine
) -> None:
    """Mandatory 1, 2, 4 and 5b: keys are per user; another user's key behaves as unused."""
    ticket, row = _n1_setup(mcp_call)
    key = "n1-shared-key-0002"
    alex = mcp_call("op.alex", "create_reply_draft", _draft_args(key, [ticket, row]))["data"]
    # rev.sam (who CAN read payroll) sends the identical request with alex's key:
    sam_same = mcp_call("rev.sam", "create_reply_draft", _draft_args(key, [ticket, row]))["data"]
    assert not sam_same["replayed"] and sam_same["draft_id"] != alex["draft_id"]
    # Different content under another user's key is treated as a brand-new request: no
    # "already used" error, so the response reveals nothing about the other user's key.
    other_key = "n1-shared-key-0003"
    mcp_call("op.alex", "create_reply_draft", _draft_args(other_key, [ticket, row]))
    sam_diff = mcp_call(
        "rev.sam", "create_reply_draft", _draft_args(other_key, [ticket], body="Totally different.")
    )["data"]
    assert "error" not in sam_diff and not sam_diff["replayed"]  # no existence signal
    with owner_engine.connect() as conn:
        owners = (
            conn.execute(
                text(
                    "SELECT created_by FROM app.drafts WHERE idempotency_key = :k ORDER BY created_by"
                ),
                {"k": key},
            )
            .scalars()
            .all()
        )
    assert owners == ["op.alex", "rev.sam"]  # unique per (tenant, created_by, key)


def test_n1_unauthorized_user_cannot_use_another_users_key_as_an_oracle(
    container: Container, token_service: TokenService, mcp_call: McpCall
) -> None:
    """Mandatory 5c: right guess and wrong guess look the same to a user without payroll:read."""
    ticket, row = _n1_setup(mcp_call)
    key = "n1-oracle-key-0004"
    mcp_call("op.alex", "create_reply_draft", _draft_args(key, [ticket, row]))
    sam_narrow = token_service.issue("rev.sam", frozenset({"tickets:read", "drafts:write"}))
    right = _call_as(container, sam_narrow, "create_reply_draft", _draft_args(key, [ticket, row]))
    wrong = _call_as(
        container,
        sam_narrow,
        "create_reply_draft",
        _draft_args(key, [ticket, {**row, "content_hash": "f" * 64}]),
    )
    unknown_key = _call_as(
        container, sam_narrow, "create_reply_draft", _draft_args("n1-unknown-key-9", [ticket, row])
    )
    assert right == wrong == unknown_key
    assert right["error"] == "invalid_request" and "not readable by this caller" in right["message"]


def test_n1_replay_compares_skill_version(mcp_call: McpCall) -> None:
    ticket, _ = _n1_setup(mcp_call)
    key = "n1-skill-key-0005"
    first = mcp_call("op.alex", "create_reply_draft", _draft_args(key, [ticket]))["data"]
    same = mcp_call("op.alex", "create_reply_draft", _draft_args(key, [ticket]))["data"]
    assert same["replayed"] and same["draft_id"] == first["draft_id"]
    changed = mcp_call(
        "op.alex",
        "create_reply_draft",
        _draft_args(key, [ticket], skill="service-ticket-triage@0.2.0"),
    )["data"]
    assert changed["error"] == "invalid_request" and "different draft" in changed["message"]


def test_n1_header_records_author_kind(mcp_call: McpCall, owner_engine: Engine) -> None:
    ticket, _ = _n1_setup(mcp_call)
    d = mcp_call("op.alex", "create_reply_draft", _draft_args(None, [ticket]))["data"]
    with owner_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT d.created_by_kind, v.author_kind, d.status FROM app.drafts d JOIN "
                "app.draft_versions v USING (draft_id) WHERE d.draft_id = :d"
            ),
            {"d": d["draft_id"]},
        ).one()
    # M3: "Reviewing now." with the ticket as evidence asserts nothing unsupported, so it passes.
    assert tuple(row) == ("human", "human", "PENDING_REVIEW")


# ---------------------------------------------------------------- final review Low-1
def test_low1_database_errors_do_not_carry_parameter_values(role_engine: RoleEngine) -> None:
    secret_text = "Confidential requester text: salary 4,200"
    with (
        pytest.raises(sqlalchemy.exc.DBAPIError) as exc_info,
        role_engine(DbRole.APP_SERVICE).begin() as conn,
    ):
        conn.execute(text("SELECT set_config('retirementops.tenant_ids', 'TEN-ALDER', true)"))
        conn.execute(
            text(
                "INSERT INTO app.drafts (draft_id, ticket_id, tenant_id, status, status_reasons, "
                "created_by, created_at, created_by_kind) VALUES ('DRF-00000000000000E1', 'ZD-1001', "
                "'TEN-ALDER', 'APPROVED', '[]', :who, now(), 'human')"
            ),
            {"who": secret_text},
        )
    message = str(exc_info.value)
    assert secret_text not in message and "hidden due to hide_parameters" in message
