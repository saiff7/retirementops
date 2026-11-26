"""Static guarantees about the Service Operations tool surface (no database needed)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from retirementops.connectors.interfaces import FORBIDDEN_METHOD_PATTERN
from retirementops.domain.authz import APPROVAL_SCOPES, ROLE_SCOPES, Role, Scope
from retirementops.mcp_servers.service_ops.schemas import input_schema
from retirementops.mcp_servers.service_ops.tools import TOOLS, TOOLS_BY_NAME

SPEC_TOOLS = {
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
}
FORBIDDEN_TOOLS = {
    "close_ticket",
    "solve_ticket",
    "change_contribution",
    "modify_enrollment",
    "send_money",
    "send_reply",
    "approve_draft",
    "run_sql",
    "http_fetch",
}
H = "a" * 64
VALID: dict[str, dict[str, Any]] = {
    "search_tickets": {},
    "get_ticket_details": {"ticket_id": "ZD-1001"},
    "get_plan_details": {"plan_id": "PLAN-001"},
    "get_participant_enrollment": {"participant_ref": "PART-0001", "plan_id": "PLAN-001"},
    "get_payroll_contributions": {
        "participant_ref": "PART-0001",
        "plan_id": "PLAN-001",
        "pay_period": "PP-2026-01",
    },
    "get_applicable_plan_rule": {
        "plan_id": "PLAN-001",
        "effective_date": "2026-02-01",
        "contribution_type": "EMPLOYER_MATCH",
    },
    "get_policy_snippets": {},
    "create_reply_draft": {
        "ticket_id": "ZD-1001",
        "body": "x",
        "skill_version": "a@1.0.0",
        "evidence_refs": [
            {
                "system": "zendesk_mock",
                "record_type": "ticket",
                "record_id": "ZD-1001",
                "content_hash": H,
            }
        ],
    },
    "propose_ticket_assignment": {
        "ticket_id": "ZD-1001",
        "target_queue": "HUMAN_TRIAGE",
        "rationale": "r",
    },
    "get_ticket_action_status": {"proposal_id": "PRP-0123456789ABCDEF"},
}


def test_registry_is_exactly_the_spec_tools() -> None:
    assert {t.name for t in TOOLS} == SPEC_TOOLS
    assert len(TOOLS) == len(TOOLS_BY_NAME) == 10


def test_registry_has_no_forbidden_tools() -> None:
    names = {t.name for t in TOOLS}
    assert not names & FORBIDDEN_TOOLS
    # The only names the shared pattern allows are the two narrow write tools' verbs.
    flagged = {n for n in names if FORBIDDEN_METHOD_PATTERN.search(n)}
    assert flagged == {"get_ticket_action_status"}  # read-only status lookup ("status" is a noun)
    assert TOOLS_BY_NAME["get_ticket_action_status"].read_only


def test_only_create_tools_write_and_none_are_destructive() -> None:
    writers = {t.name for t in TOOLS if not t.read_only}
    assert writers == {"create_reply_draft", "propose_ticket_assignment"}
    for t in TOOLS:
        assert Scope.DRAFTS_WRITE in t.scopes or t.read_only


def test_no_tool_needs_approval_or_compliance_scopes() -> None:
    for t in TOOLS:
        assert not t.scopes & APPROVAL_SCOPES
        assert not {s for s in t.scopes if s.value.startswith(("compliance:", "admin:"))}


def test_agent_role_can_use_every_tool_but_never_approve() -> None:
    agent = ROLE_SCOPES[Role.AGENT_SERVICE]
    assert all(t.scopes <= agent for t in TOOLS)
    assert not agent & APPROVAL_SCOPES


def _all_objects_closed(schema: dict[str, Any]) -> list[str]:
    open_objects = []
    nodes = [schema, *schema.get("$defs", {}).values()]
    for node in nodes:
        if node.get("type") == "object" and node.get("additionalProperties") is not False:
            open_objects.append(node.get("title", "?"))
    return open_objects


@pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t.name)
def test_advertised_schema_forbids_additional_properties(tool: Any) -> None:
    assert _all_objects_closed(input_schema(tool.input_model)) == []


@pytest.mark.parametrize("name", sorted(SPEC_TOOLS))
def test_schema_rejects_extra_fields(name: str) -> None:
    model = TOOLS_BY_NAME[name].input_model
    model.model_validate(VALID[name])  # the baseline is valid
    with pytest.raises(ValidationError, match=r"extra_forbidden|Extra inputs"):
        model.model_validate({**VALID[name], "tenant_id": "TEN-BIRCH"})


def test_nested_evidence_rejects_extra_fields() -> None:
    bad = dict(VALID["create_reply_draft"])
    bad["evidence_refs"] = [{**bad["evidence_refs"][0], "trusted": True}]
    with pytest.raises(ValidationError):
        TOOLS_BY_NAME["create_reply_draft"].input_model.model_validate(bad)


@pytest.mark.parametrize(
    ("name", "field", "value"),
    [
        ("search_tickets", "page_size", 26),
        ("search_tickets", "page_size", 0),
        ("search_tickets", "page_size", "10"),  # strict int
        ("search_tickets", "query", "x" * 201),
        ("get_policy_snippets", "limit", 11),
        ("get_ticket_details", "ticket_id", "ZD-1001' OR 1=1"),
        ("get_participant_enrollment", "participant_ref", "PART-1"),
        ("get_applicable_plan_rule", "contribution_type", "BONUS"),
        ("create_reply_draft", "body", ""),
        ("create_reply_draft", "body", "x" * 10_001),
        ("create_reply_draft", "skill_version", "latest"),
        ("propose_ticket_assignment", "target_queue", "CLOSED"),
        ("propose_ticket_assignment", "rationale", "x" * 1_001),
        ("get_ticket_action_status", "proposal_id", "DRF-0123456789ABCDEF"),
    ],
)
def test_schema_limits(name: str, field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        TOOLS_BY_NAME[name].input_model.model_validate({**VALID[name], field: value})


def test_page_size_capped_at_25() -> None:
    model = TOOLS_BY_NAME["search_tickets"].input_model
    assert model.model_validate({"page_size": 25}).page_size == 25  # type: ignore[attr-defined]
    schema = input_schema(model)
    assert schema["properties"]["page_size"]["maximum"] == 25


def test_evidence_list_is_bounded() -> None:
    model = TOOLS_BY_NAME["create_reply_draft"].input_model
    refs = [
        {**VALID["create_reply_draft"]["evidence_refs"][0], "record_id": f"ZD-{1000 + i}"}
        for i in range(51)
    ]
    with pytest.raises(ValidationError):
        model.model_validate({**VALID["create_reply_draft"], "evidence_refs": refs})


def test_ticket_text_tools_warn_that_content_is_untrusted() -> None:
    for name in ("search_tickets", "get_ticket_details"):
        assert "untrusted" in TOOLS_BY_NAME[name].description.lower()
