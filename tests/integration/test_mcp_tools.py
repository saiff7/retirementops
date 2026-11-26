"""Service Operations MCP tools end to end: real server, SDK in-memory client, real database."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
import sqlalchemy.exc
from sqlalchemy import Engine, text

from retirementops.config import DbRole
from retirementops.connectors.zendesk_mock import ZendeskMockWriter
from retirementops.domain.authz import TenantScope
from retirementops.domain.taxonomy import Queue
from retirementops.seed.generator import Dataset
from retirementops.store.workflow import draft_content_hash
from tests.conftest import McpCall

pytestmark = pytest.mark.integration
ALL_TOOLS = sorted(
    [
        "search_tickets",
        "get_ticket_details",
        "get_plan_details",
        "get_participant_enrollment",
        "get_payroll_contributions",
        "get_applicable_plan_rule",
        "get_policy_snippets",
        "create_reply_draft",
        "propose_ticket_assignment",
        "get_ticket_action_status",
    ]
)
SKILL = "service-ticket-triage@0.1.0"


def _ref(source: dict[str, str]) -> dict[str, str]:
    return {k: source[k] for k in ("system", "record_type", "record_id", "content_hash")}


def _ticket_ref(mcp_call: McpCall, user: str, ticket_id: str = "ZD-1001") -> dict[str, str]:
    """The evidence ref for the ticket being answered (required on every draft)."""
    return _ref(mcp_call(user, "get_ticket_details", {"ticket_id": ticket_id})["data"]["source"])


# Syntactically valid ticket evidence for requests that must be refused before evidence is checked.
PLACEHOLDER_REFS = [
    {
        "system": "zendesk_mock",
        "record_type": "ticket",
        "record_id": "ZD-1001",
        "content_hash": "a" * 64,
    }
]


def _count(owner_engine: Engine, sql: str, **params: Any) -> int:
    with owner_engine.connect() as conn:
        return int(conn.execute(text(sql), params).scalar_one())


def _ordinary_ticket(dataset: Dataset, tenant: str, skip: set[str]) -> str:
    labels = {lab["ticket_id"]: lab for lab in dataset.ticket_labels}
    return next(
        t["ticket_id"]
        for t in dataset.tables["zendesk_mock.tickets"]
        if t["tenant_id"] == tenant
        and t["status"] == "new"
        and not labels[t["ticket_id"]]["flags"]
        and t["ticket_id"] not in skip
    )


# ---------------------------------------------------------------- discovery and scopes
@pytest.mark.parametrize(
    ("user", "expected"),
    [
        ("op.alex", ALL_TOOLS),
        ("rev.sam", ALL_TOOLS),
        ("agent.worker", ALL_TOOLS),
        ("comp.jo", []),
        ("admin.kim", []),
    ],
)
def test_tools_list_is_filtered_by_scope(
    mcp_tools: Callable[[str], list[str]], user: str, expected: list[str]
) -> None:
    assert mcp_tools(user) == expected


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("get_ticket_details", {"ticket_id": "ZD-1001"}),
        ("search_tickets", {}),
        ("get_plan_details", {"plan_id": "PLAN-001"}),
        (
            "create_reply_draft",
            {"ticket_id": "ZD-1001", "body": "x", "evidence_refs": [], "skill_version": SKILL},
        ),
    ],
)
def test_missing_scope_403(
    mcp_call: McpCall, owner_engine: Engine, tool: str, args: dict[str, Any]
) -> None:
    before = _count(
        owner_engine,
        "SELECT count(*) FROM app.audit_events "
        "WHERE event_type = 'access.denied' AND details->>'tool' = :t",
        t=tool,
    )
    r = mcp_call("comp.jo", tool, args)  # compliance reviewer: no service-ops scopes
    assert r["is_error"] and r["data"]["error"] == "forbidden"
    assert "missing scope" in r["data"]["message"]
    after = _count(
        owner_engine,
        "SELECT count(*) FROM app.audit_events "
        "WHERE event_type = 'access.denied' AND details->>'tool' = :t",
        t=tool,
    )
    assert after == before + 1


def test_unknown_and_forbidden_tool_names_are_refused_and_audited(
    mcp_call: McpCall, owner_engine: Engine
) -> None:
    for name in ("close_ticket", "change_contribution", "send_money", "approve_draft"):
        r = mcp_call("op.alex", name, {"ticket_id": "ZD-1001"})
        assert r["is_error"] and r["data"]["error"] == "unknown_tool"
    n = _count(
        owner_engine,
        "SELECT count(*) FROM app.audit_events WHERE event_type = "
        "'access.denied' AND details->>'policy_reason' = 'unknown tool'",
    )
    assert n >= 4


@pytest.mark.parametrize(
    ("args", "where"),
    [
        ({"ticket_id": "ZD-1001", "tenant_id": "TEN-BIRCH"}, "tenant_id:extra_forbidden"),
        ({"ticket_id": "1001"}, "ticket_id:string_pattern_mismatch"),
        ({}, "ticket_id:missing"),
    ],
)
def test_bad_arguments_report_location_only(
    mcp_call: McpCall, args: dict[str, Any], where: str
) -> None:
    r = mcp_call("op.alex", "get_ticket_details", args)
    assert r["is_error"] and r["data"]["error"] == "invalid_arguments"
    assert where in r["data"]["message"]
    assert "TEN-BIRCH" not in r["text"]  # submitted values are never echoed


def test_page_size_capped(mcp_call: McpCall) -> None:
    assert mcp_call("op.alex", "search_tickets", {"page_size": 26})["is_error"]
    ok = mcp_call("op.alex", "search_tickets", {"page_size": 25})
    assert not ok["is_error"] and len(ok["data"]["items"]) == 25


# ---------------------------------------------------------------- read tools
def test_read_tools_return_source_metadata(mcp_call: McpCall) -> None:
    calls = [
        ("get_ticket_details", {"ticket_id": "ZD-1001"}),
        ("get_plan_details", {"plan_id": "PLAN-001"}),
        ("get_participant_enrollment", {"participant_ref": "PART-0003", "plan_id": "PLAN-001"}),
        (
            "get_payroll_contributions",
            {"participant_ref": "PART-0002", "plan_id": "PLAN-001", "pay_period": "PP-2026-03"},
        ),
        (
            "get_applicable_plan_rule",
            {
                "plan_id": "PLAN-001",
                "effective_date": "2026-03-01",
                "contribution_type": "EMPLOYER_MATCH",
            },
        ),
    ]
    out = {name: mcp_call("rev.sam", name, args) for name, args in calls}
    assert not any(r["is_error"] for r in out.values())
    assert out["get_ticket_details"]["data"]["source"]["system"] == "zendesk_mock"
    assert len(out["get_participant_enrollment"]["data"]["enrollments"]) == 2  # 5% then 8%
    row = out["get_payroll_contributions"]["data"]["rows"][0]
    assert row["employee_contribution"]["amount"] == row["employee_contribution"]["display"][1:]
    assert row["source"]["record_type"] == "payroll_row"
    rule = out["get_applicable_plan_rule"]["data"]
    assert rule["status"] == "SELECTED" and rule["rule"]["rule_version"] == "v2"  # boundary day


def test_rule_conflict_is_reported_not_resolved(mcp_call: McpCall) -> None:
    r = mcp_call(
        "rev.sam",
        "get_applicable_plan_rule",
        {
            "plan_id": "PLAN-004",
            "effective_date": "2026-03-15",
            "contribution_type": "EMPLOYER_MATCH",
        },
    )
    assert r["data"]["status"] == "CONFLICT" and r["data"]["rule"] is None
    assert r["data"]["candidate_rule_ids"] == ["RULE-009", "RULE-010"]


def test_ticket_text_is_marked_untrusted_and_restricted_patterns_masked(
    mcp_call: McpCall, dataset: Dataset
) -> None:
    ssn_ticket = next(
        lab["ticket_id"]
        for lab in dataset.ticket_labels
        if "CONTAINS_RESTRICTED_PATTERN" in lab["flags"] and lab["tenant_id"] == "TEN-ALDER"
    )
    r = mcp_call("op.alex", "get_ticket_details", {"ticket_id": ssn_ticket})
    body = r["data"]["body"]
    assert body["trust"] == "UNTRUSTED_REQUESTER_TEXT" and body["contains_restricted_pattern"]
    assert "[REDACTED_SSN]" in body["text"] and "000-12-3456" not in r["text"]


def test_prompt_injection_text_is_returned_as_data_only(
    mcp_call: McpCall, dataset: Dataset
) -> None:
    inj = next(
        lab["ticket_id"]
        for lab in dataset.ticket_labels
        if "PROMPT_INJECTION" in lab["flags"] and lab["tenant_id"] == "TEN-ALDER"
    )
    r = mcp_call("op.alex", "get_ticket_details", {"ticket_id": inj})
    assert r["data"]["body"]["trust"] == "UNTRUSTED_REQUESTER_TEXT"
    assert "ignore your previous instructions" in r["data"]["body"]["text"]  # returned, not obeyed
    # M2 guarantees the blast radius (scoped reads, no consequential tools). Whether a model
    # resists the injection is measured by the M3/M4 prompt-injection eval cases.


def test_policy_snippets_only_in_force_versions(mcp_call: McpCall) -> None:
    every = mcp_call("op.alex", "get_policy_snippets", {"limit": 10})["data"]["snippets"]
    rest = mcp_call("op.alex", "get_policy_snippets", {"limit": 10, "query": "e"})["data"]
    ids = {s["snippet_id"] for s in every} | {s["snippet_id"] for s in rest["snippets"]}
    assert "POL-002" not in ids and "POL-016" not in ids  # superseded / not yet effective
    contrib = mcp_call("op.alex", "get_policy_snippets", {"category": "CONTRIBUTIONS"})["data"]
    assert [(s["snippet_id"], s["version"]) for s in contrib["snippets"]] == [
        ("POL-001", "v2"),
        ("POL-015", "v1"),
    ]
    assert len(mcp_call("op.alex", "get_policy_snippets", {"limit": 3})["data"]["snippets"]) == 3


# ---------------------------------------------------------------- tenant isolation over MCP
ALDER_READS = [
    ("get_ticket_details", {"ticket_id": "ZD-1001"}),
    ("get_plan_details", {"plan_id": "PLAN-001"}),
    ("get_participant_enrollment", {"participant_ref": "PART-0001", "plan_id": "PLAN-001"}),
    ("get_participant_enrollment", {"participant_ref": "PART-0001", "plan_id": "PLAN-005"}),
    (
        "get_payroll_contributions",
        {"participant_ref": "PART-0001", "plan_id": "PLAN-001", "pay_period": "PP-2026-02"},
    ),
    (
        "get_applicable_plan_rule",
        {
            "plan_id": "PLAN-001",
            "effective_date": "2026-02-01",
            "contribution_type": "EMPLOYER_MATCH",
        },
    ),
]


@pytest.mark.parametrize(("tool", "args"), ALDER_READS)
def test_cross_tenant_read_denied_through_mcp(
    mcp_call: McpCall, tool: str, args: dict[str, Any]
) -> None:
    r = mcp_call("op.casey", tool, args)  # Cobalt-only operator
    assert r["is_error"] and r["data"]["error"] == "not_found"
    assert "Alder" not in r["text"]


def test_cross_tenant_search_and_writes_denied(
    mcp_call: McpCall, dataset: Dataset, owner_engine: Engine
) -> None:
    cobalt = {
        t["ticket_id"]
        for t in dataset.tables["zendesk_mock.tickets"]
        if t["tenant_id"] == "TEN-COBALT"
    }
    items = mcp_call("op.casey", "search_tickets", {"page_size": 25})["data"]["items"]
    assert items and {i["ticket_id"] for i in items} <= cobalt
    before = _count(owner_engine, "SELECT count(*) FROM app.drafts")
    for tool, args in (
        (
            "create_reply_draft",
            {
                "ticket_id": "ZD-1001",
                "body": "x",
                "evidence_refs": PLACEHOLDER_REFS,
                "skill_version": SKILL,
            },
        ),
        (
            "propose_ticket_assignment",
            {"ticket_id": "ZD-1001", "target_queue": "HUMAN_TRIAGE", "rationale": "x"},
        ),
    ):
        r = mcp_call("op.casey", tool, args)
        assert r["data"]["error"] == "not_found"
    assert _count(owner_engine, "SELECT count(*) FROM app.drafts") == before


def test_agent_without_run_token_tenant_sees_nothing(mcp_call: McpCall) -> None:
    """agent.worker has no standing tenant assignment; run-scoped tokens arrive in M3. Fail closed."""
    assert mcp_call("agent.worker", "search_tickets", {})["data"]["items"] == []
    assert (
        mcp_call("agent.worker", "get_ticket_details", {"ticket_id": "ZD-1001"})["data"]["error"]
        == "not_found"
    )


# ---------------------------------------------------------------- drafts (rows only)
def test_create_reply_draft_creates_rows_only(
    mcp_call: McpCall, owner_engine: Engine, dataset: Dataset
) -> None:
    tid = _ordinary_ticket(dataset, "TEN-ALDER", {"ZD-1001"})
    ticket = mcp_call("op.alex", "get_ticket_details", {"ticket_id": tid})["data"]
    rows = mcp_call(
        "op.alex",
        "get_payroll_contributions",
        {"participant_ref": "PART-0002", "plan_id": "PLAN-001", "pay_period": "PP-2026-03"},
    )["data"]
    policy = mcp_call("op.alex", "get_policy_snippets", {"category": "COMMUNICATIONS", "limit": 1})[
        "data"
    ]["snippets"][0]
    refs = [
        _ref(ticket["source"]),
        _ref(rows["rows"][0]["source"]),
        _ref(rows["pay_period"]["source"]),
        _ref(policy["source"]),
    ]
    snapshot = "SELECT status || version || coalesce(assignee_queue, '') FROM zendesk_mock.tickets WHERE ticket_id = :t"
    with owner_engine.connect() as conn:
        before = conn.execute(text(snapshot), {"t": tid}).scalar_one()
    comments = _count(owner_engine, "SELECT count(*) FROM zendesk_mock.ticket_comments")

    body = "Thank you. We are reviewing the contribution for this pay period."
    r = mcp_call(
        "op.alex",
        "create_reply_draft",
        {"ticket_id": tid, "body": body, "evidence_refs": refs, "skill_version": SKILL},
    )
    d = r["data"]
    assert not r["is_error"] and d["sent"] is False and d["evidence_count"] == 4
    assert d["status"] == "NEEDS_MANUAL_REVIEW"  # fail closed until the M3 validator
    assert d["content_hash"] == draft_content_hash(ticket_id=tid, body=body, evidence_refs=refs)
    with owner_engine.connect() as conn:
        stored = conn.execute(
            text(
                "SELECT v.version, v.author_principal, v.author_kind, v.ticket_version, d.tenant_id "
                "FROM app.drafts d JOIN app.draft_versions v USING (draft_id) "
                "WHERE d.draft_id = :d"
            ),
            {"d": d["draft_id"]},
        ).one()
        after = conn.execute(text(snapshot), {"t": tid}).scalar_one()
        audit = conn.execute(
            text(
                "SELECT details::text FROM app.audit_events WHERE event_type = 'draft.created' "
                "AND target_ref = :d"
            ),
            {"d": d["draft_id"]},
        ).scalar_one()
    assert tuple(stored) == (1, "op.alex", "human", ticket["version"], "TEN-ALDER")
    assert after == before  # ticket untouched: nothing sent, assigned or closed
    assert _count(owner_engine, "SELECT count(*) FROM zendesk_mock.ticket_comments") == comments
    assert body not in audit and d["content_hash"] in audit


@pytest.mark.parametrize(
    "problem", ["cross_tenant", "tampered", "unknown", "unsupported", "expired_policy", "duplicate"]
)
def test_draft_evidence_is_verified_server_side(
    mcp_call: McpCall, owner_engine: Engine, problem: str
) -> None:
    ticket = mcp_call("rev.sam", "get_ticket_details", {"ticket_id": "ZD-1001"})["data"]
    good = _ref(ticket["source"])
    if problem == "cross_tenant":  # a Birchway payroll row cited on an Alder ticket
        birch = mcp_call(
            "rev.sam",
            "get_payroll_contributions",
            {"participant_ref": "PART-0023", "plan_id": "PLAN-003", "pay_period": "PP-2026-02"},
        )["data"]["rows"][0]["source"]
        refs = [good, _ref(birch)]
    elif problem == "tampered":
        refs = [{**good, "content_hash": "0" * 64}]
    elif problem == "unknown":
        refs = [good, {**good, "record_id": "ZD-9999"}]
    elif problem == "unsupported":
        refs = [good, {**good, "system": "app", "record_type": "drafts"}]
    elif problem == "expired_policy":
        refs = [
            good,
            {
                "system": "servicekb_mock",
                "record_type": "policy_snippet",
                "record_id": "POL-002",
                "content_hash": "a" * 64,
            },
        ]
    else:
        refs = [good, good]
    before = _count(owner_engine, "SELECT count(*) FROM app.drafts")
    r = mcp_call(
        "rev.sam",
        "create_reply_draft",
        {"ticket_id": "ZD-1001", "body": "x", "evidence_refs": refs, "skill_version": SKILL},
    )
    assert r["is_error"] and r["data"]["error"] == "invalid_request"
    assert "evidence rejected" in r["data"]["message"]
    assert _count(owner_engine, "SELECT count(*) FROM app.drafts") == before


def test_stale_evidence_is_rejected(
    mcp_call: McpCall, role_engine: Callable[[DbRole], Engine], dataset: Dataset
) -> None:
    tid = _ordinary_ticket(dataset, "TEN-BIRCH", set())
    ticket = mcp_call("rev.sam", "get_ticket_details", {"ticket_id": tid})["data"]
    # The ticket changes in Zendesk after the agent read it (here: reassigned).
    ZendeskMockWriter(role_engine(DbRole.ZENDESK_WRITER)).apply_assignment(
        TenantScope(frozenset({"TEN-BIRCH"})),
        ticket_id=tid,
        queue=Queue.HUMAN_TRIAGE,
        expected_version=ticket["version"],
    )
    r = mcp_call(
        "rev.sam",
        "create_reply_draft",
        {
            "ticket_id": tid,
            "body": "x",
            "evidence_refs": [_ref(ticket["source"])],
            "skill_version": SKILL,
        },
    )
    assert r["data"]["error"] == "invalid_request" and "hash does not match" in r["data"]["message"]


def test_drafts_refused_for_closed_tickets_and_restricted_text(
    mcp_call: McpCall, dataset: Dataset
) -> None:
    closed = next(
        t["ticket_id"]
        for t in dataset.tables["zendesk_mock.tickets"]
        if t["status"] in ("solved", "closed") and t["tenant_id"] == "TEN-ALDER"
    )
    r = mcp_call(
        "op.alex",
        "create_reply_draft",
        {
            "ticket_id": closed,
            "body": "x",
            "evidence_refs": PLACEHOLDER_REFS,
            "skill_version": SKILL,
        },
    )
    assert r["data"]["error"] == "invalid_request" and "solved or closed" in r["data"]["message"]
    r = mcp_call(
        "op.alex",
        "create_reply_draft",
        {
            "ticket_id": "ZD-1001",
            "body": "Your SSN 000-12-3456 is on file.",
            "evidence_refs": PLACEHOLDER_REFS,
            "skill_version": SKILL,
        },
    )
    assert r["data"]["error"] == "invalid_request" and "000-12-3456" not in r["text"]


# ---------------------------------------------------------------- proposals (rows only)
def test_propose_assignment_creates_proposal_only_and_deduplicates(
    mcp_call: McpCall, owner_engine: Engine, dataset: Dataset
) -> None:
    tid = _ordinary_ticket(dataset, "TEN-COBALT", set())
    args = {"ticket_id": tid, "target_queue": "PAYROLL_OPERATIONS", "rationale": "Payroll issue"}
    first = mcp_call("op.casey", "propose_ticket_assignment", args)["data"]
    again = mcp_call("op.casey", "propose_ticket_assignment", {**args, "rationale": "retry"})[
        "data"
    ]
    assert first["status"] == "PROPOSED" and first["applied"] is False
    assert not first["deduplicated"] and again["deduplicated"]
    assert again["proposal_id"] == first["proposal_id"]
    with owner_engine.connect() as conn:
        queue = conn.execute(
            text("SELECT assignee_queue FROM zendesk_mock.tickets WHERE ticket_id = :t"), {"t": tid}
        ).scalar_one()
    assert queue is None  # not reassigned
    status = mcp_call(
        "op.casey", "get_ticket_action_status", {"proposal_id": first["proposal_id"]}
    )["data"]
    assert status["status"] == "PROPOSED" and status["target_queue"] == "PAYROLL_OPERATIONS"
    # Another tenant's user cannot see it.
    other = mcp_call("op.alex", "get_ticket_action_status", {"proposal_id": first["proposal_id"]})
    assert other["data"]["error"] == "not_found"


# ---------------------------------------------------------------- database-level protection
@pytest.mark.parametrize(
    "stmt",
    [
        "UPDATE app.drafts SET status = 'APPROVED'",
        "DELETE FROM app.drafts",
        "UPDATE app.action_proposals SET status = 'APPROVED'",
        "UPDATE app.draft_versions SET body = 'changed'",
    ],
)
def test_app_role_cannot_approve_or_alter_drafts(
    role_engine: Callable[[DbRole], Engine], stmt: str
) -> None:
    with (
        pytest.raises(sqlalchemy.exc.ProgrammingError, match="permission denied"),
        role_engine(DbRole.APP_SERVICE).begin() as conn,
    ):
        conn.execute(
            text(
                "SELECT set_config('retirementops.tenant_ids', "
                "'TEN-ALDER,TEN-BIRCH,TEN-COBALT', true)"
            )
        )
        conn.execute(text(stmt))


@pytest.mark.parametrize(
    "stmt",
    [
        "UPDATE app.draft_versions SET body = 'rewritten'",
        "DELETE FROM app.draft_versions",
        "TRUNCATE app.draft_versions CASCADE",
        "TRUNCATE app.drafts CASCADE",
        "TRUNCATE app.action_proposals",
    ],
)
def test_draft_versions_tamper_resistant_for_owner(
    owner_engine: Engine, mcp_call: McpCall, stmt: str
) -> None:
    """Review Low-1: UPDATE, DELETE and TRUNCATE are all refused, even for the table owner.
    Tamper-resistant, not immutable: the owner can still disable triggers (ADR-013)."""
    r = mcp_call(
        "rev.sam",
        "create_reply_draft",
        {
            "ticket_id": "ZD-1001",
            "body": "v1",
            "skill_version": SKILL,
            "evidence_refs": [_ticket_ref(mcp_call, "rev.sam")],
        },
    )
    assert not r["is_error"]
    with pytest.raises(sqlalchemy.exc.DBAPIError, match="immutable"), owner_engine.begin() as conn:
        conn.execute(text(stmt))


def test_drafts_invisible_without_tenant_scope(
    role_engine: Callable[[DbRole], Engine], mcp_call: McpCall
) -> None:
    r = mcp_call(
        "rev.sam",
        "create_reply_draft",
        {
            "ticket_id": "ZD-1001",
            "body": "x",
            "skill_version": SKILL,
            "evidence_refs": [_ticket_ref(mcp_call, "rev.sam")],
        },
    )
    assert not r["is_error"]
    with role_engine(DbRole.APP_SERVICE).begin() as conn:
        for table in ("app.drafts", "app.draft_versions", "app.action_proposals"):
            assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0


# ---------------------------------------------------------------- per-call re-verification
def test_stdio_style_token_is_reverified_on_every_call(
    container: Any, bearer: Callable[[str], dict[str, str]], owner_engine: Engine
) -> None:
    import anyio
    from mcp.client.client import Client

    from retirementops.mcp_servers.service_ops.server import ServiceOpsMcp, TokenPrincipalSource

    token = bearer("op.alex")["Authorization"].split()[1]
    server = ServiceOpsMcp(container, TokenPrincipalSource(container, token))

    async def go() -> tuple[bool, dict[str, Any] | None]:
        async with Client(server.server) as client:
            ok = await client.call_tool("search_tickets", {})
            with owner_engine.begin() as conn:
                conn.execute(text("UPDATE app.users SET active = false WHERE user_id = 'op.alex'"))
            try:
                denied = await client.call_tool("search_tickets", {})
            finally:
                with owner_engine.begin() as conn:
                    conn.execute(
                        text("UPDATE app.users SET active = true WHERE user_id = 'op.alex'")
                    )
            return bool(ok.is_error), denied.structured_content

    ok_error, denied = anyio.run(go)
    assert ok_error is False
    assert denied == {"error": "unauthenticated", "message": "authentication required"}


def test_metrics_count_outcomes(container: Any, bearer: Callable[[str], dict[str, str]]) -> None:
    import anyio
    from mcp.client.client import Client

    from retirementops.mcp_servers.service_ops.server import ServiceOpsMcp, TokenPrincipalSource

    server = ServiceOpsMcp(
        container, TokenPrincipalSource(container, bearer("op.alex")["Authorization"].split()[1])
    )

    async def go() -> None:
        async with Client(server.server) as client:
            await client.call_tool("get_plan_details", {"plan_id": "PLAN-001"})
            await client.call_tool("get_plan_details", {"plan_id": "PLAN-005"})  # not hers
            await client.call_tool("get_plan_details", {"plan_id": "bad"})

    anyio.run(go)
    snap = server.metrics.snapshot()["get_plan_details"]
    assert snap["calls"] == 3
    assert snap["outcomes"] == {"invalid_arguments": 1, "not_found": 1, "ok": 1}
    assert snap["p95_ms"] >= snap["p50_ms"] > 0


# ---------------------------------------------------------------- coverage of remaining paths
def test_all_evidence_types_verify(mcp_call: McpCall) -> None:
    plan = mcp_call("rev.sam", "get_plan_details", {"plan_id": "PLAN-001"})["data"]
    rule = mcp_call(
        "rev.sam",
        "get_applicable_plan_rule",
        {
            "plan_id": "PLAN-001",
            "effective_date": "2026-02-01",
            "contribution_type": "EMPLOYER_MATCH",
        },
    )["data"]["rule"]
    enr = mcp_call(
        "rev.sam",
        "get_participant_enrollment",
        {"participant_ref": "PART-0001", "plan_id": "PLAN-001"},
    )["data"]["enrollments"]
    refs = [
        _ticket_ref(mcp_call, "rev.sam"),
        _ref(plan["source"]),
        _ref(rule["source"]),
        _ref(enr[0]["source"]),
    ]
    r = mcp_call(
        "rev.sam",
        "create_reply_draft",
        {"ticket_id": "ZD-1001", "body": "x", "evidence_refs": refs, "skill_version": SKILL},
    )
    assert not r["is_error"] and r["data"]["evidence_count"] == 4
    bad_row = {
        "system": "payroll_mock",
        "record_type": "payroll_row",
        "record_id": "not-a-row-id",
        "content_hash": "a" * 64,
    }
    r = mcp_call(
        "rev.sam",
        "create_reply_draft",
        {
            "ticket_id": "ZD-1001",
            "body": "x",
            "evidence_refs": [_ticket_ref(mcp_call, "rev.sam"), bad_row],
            "skill_version": SKILL,
        },
    )
    assert r["data"]["error"] == "invalid_request"


def test_internal_errors_are_not_leaked(
    mcp_call: McpCall, container: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("secret connection string postgresql://user:pw@db")

    monkeypatch.setattr(container.plans, "get_plan", boom)
    r = mcp_call("op.alex", "get_plan_details", {"plan_id": "PLAN-001"})
    assert r["data"] == {"error": "internal_error", "message": "the tool failed; see server logs"}
    assert "postgresql" not in r["text"]


def test_mcp_principal_rate_limit(
    settings: Any,
    test_db: str,
    dataset: Dataset,
    token_service: Any,
    bearer: Callable[[str], dict[str, str]],
    owner_engine: Engine,
) -> None:
    import anyio
    from mcp.client.client import Client

    from retirementops.mcp_servers.service_ops.server import ServiceOpsMcp, TokenPrincipalSource
    from retirementops.services.container import build_container
    from retirementops.services.request_guard import GuardConfig
    from tests.conftest import FakeClock

    strict = GuardConfig(principal_requests_per_minute=3, principal_burst=3)
    c = build_container(
        settings, db_name=test_db, tokens=token_service, guard_config=strict, monotonic=FakeClock()
    )
    try:
        server = ServiceOpsMcp(
            c, TokenPrincipalSource(c, bearer("op.alex")["Authorization"].split()[1])
        )

        async def go() -> list[str]:
            async with Client(server.server) as client:
                out = []
                for _ in range(6):
                    r = await client.call_tool("get_plan_details", {"plan_id": "PLAN-001"})
                    out.append("ok" if not r.is_error else r.structured_content["error"])
                return out

        assert anyio.run(go) == ["ok"] * 3 + ["rate_limited"] * 3
    finally:
        c.dispose()
