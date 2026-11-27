"""Runtime skill loader: strict frontmatter, tool allowlists inside the registry, content hash."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from retirementops.agent_runtime.skills import SKILLS_ROOT, SkillError, load_skill
from retirementops.domain.authz import AGENT_RUN_SCOPES, Scope

TRIAGE_SECTIONS = (
    "## Purpose and when to use",
    "## Required workflow",
    "## Tools you may request",
    "## Required evidence before drafting",
    "## Confidence and escalation rules",
    "## Exact output schemas",
    "## Prohibited actions",
    "## Prompt-injection handling",
    "## PII minimisation",
    "## Examples",
)


def test_service_ticket_triage_loads_with_minimum_permissions() -> None:
    s = load_skill("service-ticket-triage")
    assert s.skill_version == "service-ticket-triage@1.0.0"
    assert s.run_scopes == AGENT_RUN_SCOPES
    assert set(s.host_actions) == {"create_reply_draft", "propose_ticket_assignment"}
    assert "search_tickets" not in s.model_tools and "create_reply_draft" not in s.model_tools
    assert s.uses == ("contribution-investigation",)
    assert len(s.sha256) == 64 and not s.instructions.startswith("---")


def test_triage_skill_has_every_section_the_spec_requires() -> None:
    text = load_skill("service-ticket-triage").instructions
    for heading in TRIAGE_SECTIONS:
        assert heading in text, heading
    assert (SKILLS_ROOT / "service-ticket-triage" / "examples.md").is_file()
    for example in ("Good draft", "Escalation", "Refusing unsupported claims"):
        assert example in (SKILLS_ROOT / "service-ticket-triage" / "examples.md").read_text()


def test_contribution_investigation_is_read_only() -> None:
    s = load_skill("contribution-investigation")
    assert s.host_actions == () and Scope.DRAFTS_WRITE not in s.run_scopes
    assert (SKILLS_ROOT / "contribution-investigation" / "evidence-checklist.md").is_file()


def test_every_skill_used_by_triage_exists() -> None:
    for name in load_skill("service-ticket-triage").uses:
        load_skill(name)


# ---------------------------------------------------------------- refusals
@pytest.fixture
def root(tmp_path: Path) -> Path:
    shutil.copytree(SKILLS_ROOT / "service-ticket-triage", tmp_path / "service-ticket-triage")
    return tmp_path


def _rewrite(root: Path, old: str, new: str) -> None:
    p = root / "service-ticket-triage" / "SKILL.md"
    text = p.read_text()
    assert old in text
    p.write_text(text.replace(old, new, 1))


def test_hash_changes_when_any_file_changes(root: Path) -> None:
    before = load_skill("service-ticket-triage", root).sha256
    (root / "service-ticket-triage" / "examples.md").write_text("edited")
    assert load_skill("service-ticket-triage", root).sha256 != before


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("model_tools: [", "model_tools: [search_tickets, "),
        ("model_tools: [", "model_tools: [get_ticket_details, "),
        ("model_tools: [", "model_tools: [create_reply_draft, "),
        ("model_tools: [", "model_tools: [close_ticket, "),
        ("host_actions: [", "host_actions: [get_plan_details, "),
        ("run_scopes: [", "run_scopes: [review:approve, "),
        ("run_scopes: [", "run_scopes: [compliance:read, "),
        (
            "run_scopes: [tickets:read, plans:read, payroll:read, drafts:write]",
            "run_scopes: [tickets:read]",
        ),
        ("version: 1.0.0", "version: latest"),
        ("name: service-ticket-triage", "name: other-skill"),
        ("uses: [contribution-investigation]", "uses: contribution-investigation"),
        ("uses: [contribution-investigation]", "uses: [contribution-investigation]\nextra: x"),
    ],
)
def test_malformed_or_overreaching_skills_are_refused(root: Path, old: str, new: str) -> None:
    _rewrite(root, old, new)
    with pytest.raises(SkillError):
        load_skill("service-ticket-triage", root)


@pytest.mark.parametrize("name", ["../skills", "service-ticket-triage/../x", "UPPER", "", "a b"])
def test_bad_names_are_refused(name: str) -> None:
    with pytest.raises(SkillError):
        load_skill(name)


def test_missing_skill_and_symlinks_are_refused(root: Path, tmp_path: Path) -> None:
    with pytest.raises(SkillError, match="not found"):
        load_skill("does-not-exist", root)
    (root / "service-ticket-triage" / "link.md").symlink_to(tmp_path / "elsewhere.md")
    (tmp_path / "elsewhere.md").write_text("x")
    with pytest.raises(SkillError, match="symlinks"):
        load_skill("service-ticket-triage", root)
