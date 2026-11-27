"""Load runtime business skills from `skills/<name>/` (ADR-005). Content-hashed and validated.

A skill is process knowledge (Markdown) plus a small frontmatter header. The loader is strict:
- the name must be a plain slug and resolve inside the skills root (no path tricks or symlinks);
- frontmatter keys are a fixed set, the version is semantic, the name matches the folder;
- `model_tools` (offered to the model) must be read-only tools from the MCP registry, and never
  ticket search; `host_actions` (run by the host itself) must be write tools from the registry;
- `run_scopes` must fit inside what an agent run may hold.
Every file in the folder feeds one SHA-256, recorded on each run, so a run is tied to exact text.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from retirementops.config import ROOT
from retirementops.domain.authz import AGENT_RUN_SCOPES, Scope
from retirementops.mcp_servers.service_ops.tools import TOOLS_BY_NAME

SKILLS_ROOT = ROOT / "skills"
MAX_SKILL_BYTES = 64 * 1024
_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_LIST = re.compile(r"^\[(.*)\]$")
_KEYS = frozenset(
    {"name", "version", "description", "model_tools", "host_actions", "run_scopes", "uses"}
)
# Tools a model is never offered, even if a skill lists them (no cross-ticket browsing).
NEVER_MODEL_TOOLS = frozenset({"search_tickets", "get_ticket_details"})


class SkillError(Exception):
    """A skill is missing, malformed or asks for more than the runtime allows."""


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    version: str
    description: str
    model_tools: tuple[str, ...]
    host_actions: tuple[str, ...]
    run_scopes: frozenset[Scope]
    uses: tuple[str, ...]
    instructions: str  # SKILL.md body without the frontmatter
    sha256: str  # over every file in the skill folder

    @property
    def skill_version(self) -> str:
        """The `name@x.y.z` string stored on drafts and runs."""
        return f"{self.name}@{self.version}"


def _parse_frontmatter(text: str) -> tuple[dict[str, str | tuple[str, ...]], str]:
    if not text.startswith("---\n"):
        raise SkillError("SKILL.md must start with a frontmatter block")
    head, sep, body = text[4:].partition("\n---\n")
    if not sep:
        raise SkillError("SKILL.md frontmatter is not closed")
    out: dict[str, str | tuple[str, ...]] = {}
    for line in head.splitlines():
        key, colon, raw = line.partition(":")
        key, raw = key.strip(), raw.strip()
        if not colon or key not in _KEYS or key in out:
            raise SkillError("unknown, duplicate or malformed frontmatter key")
        m = _LIST.match(raw)
        if key in {"model_tools", "host_actions", "run_scopes", "uses"}:
            if not m:
                raise SkillError(f"{key} must be a [list]")
            out[key] = tuple(v.strip() for v in m.group(1).split(",") if v.strip())
        else:
            out[key] = raw
    if set(out) != _KEYS:
        raise SkillError("frontmatter must contain exactly: " + ", ".join(sorted(_KEYS)))
    return out, body


def _folder_hash(folder: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        if path.is_symlink():
            raise SkillError("skill folders may not contain symlinks")
        data = path.read_bytes()
        if len(data) > MAX_SKILL_BYTES:
            raise SkillError("skill file too large")
        rel = path.relative_to(folder).as_posix().encode()
        digest.update(len(rel).to_bytes(4, "big") + rel + len(data).to_bytes(8, "big") + data)
    return digest.hexdigest()


def load_skill(name: str, root: Path = SKILLS_ROOT) -> Skill:
    if not isinstance(name, str) or not _NAME.match(name):
        raise SkillError("invalid skill name")
    base = root.resolve()
    folder = (root / name).resolve()
    if folder.parent != base or folder.is_symlink() or not (folder / "SKILL.md").is_file():
        raise SkillError("skill not found")
    meta, body = _parse_frontmatter((folder / "SKILL.md").read_text(encoding="utf-8"))

    version, description = str(meta["version"]), str(meta["description"])
    if meta["name"] != name or not _VERSION.match(version) or not description:
        raise SkillError("frontmatter name, version or description is invalid")
    model_tools, host_actions = tuple(meta["model_tools"]), tuple(meta["host_actions"])
    for tool in model_tools:
        spec = TOOLS_BY_NAME.get(tool)
        if spec is None or not spec.read_only or tool in NEVER_MODEL_TOOLS:
            raise SkillError("model_tools may list only read-only registry tools (no ticket reads)")
    for tool in host_actions:
        spec = TOOLS_BY_NAME.get(tool)
        if spec is None or spec.read_only:
            raise SkillError("host_actions may list only registry write tools")
    known = {s.value: s for s in Scope}
    try:
        scopes = frozenset(known[s] for s in meta["run_scopes"])
    except KeyError as exc:
        raise SkillError("unknown scope in run_scopes") from exc
    if not scopes or not scopes <= AGENT_RUN_SCOPES:
        raise SkillError("run_scopes exceed what an agent run may hold")
    for tool in (*model_tools, *host_actions):
        if not TOOLS_BY_NAME[tool].scopes <= scopes:
            raise SkillError("a listed tool needs a scope the skill does not request")
    uses = tuple(meta["uses"])
    if any(not _NAME.match(u) or u == name for u in uses):
        raise SkillError("invalid entry in uses")
    return Skill(
        name=name,
        version=version,
        description=description,
        model_tools=model_tools,
        host_actions=host_actions,
        run_scopes=scopes,
        uses=uses,
        instructions=body.strip(),
        sha256=_folder_hash(folder),
    )
