"""`agent.yaml` — the package's contract.

Validation is deliberate: a manifest naming a tool the package does not
implement, or a prompt file that is not there, should fail with a sentence a
human can act on, not a KeyError from inside a run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "AgentSpec",
    "ManifestError",
    "MANIFEST_NAME",
    "SkillMeta",
    "parse_frontmatter",
    "strip_frontmatter",
]

MANIFEST_NAME = "agent.yaml"

# Agent Skills spec (https://agentskills.io/specification): name is lowercase letters,
# digits and hyphens, max 64 chars, and must equal the skill's folder name — the same
# rule teacup-agent's own skills.py enforces, so a skill package validated by either
# repo means the same thing. description is capped at 1024 chars: one catalog line,
# not a paragraph.
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_SKILL_MAX_NAME_LEN = 64
_SKILL_MAX_DESCRIPTION_LEN = 1024


@dataclass
class SkillMeta:
    """A skill's Agent Skills frontmatter — the open format a folder + `SKILL.md`
    already follows here, shared with teacup-agent and the wider ecosystem (OpenAI
    Codex CLI, Microsoft Agent Framework, Cursor, GitHub Copilot). Exposes the spec's
    optional fields instead of silently dropping them the way `skill_body()`'s plain
    `strip_frontmatter()` always has."""

    name: str
    description: str
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    allowed_tools: tuple[str, ...] = ()


class ManifestError(ValueError):
    """The manifest is missing, malformed, or inconsistent with the package."""


@dataclass
class AgentSpec:
    """A parsed manifest, plus where it was read from."""

    name: str
    version: str
    description: str
    framework: str
    # Unused when framework == "teacup" (the native loop needs no external command).
    # For any other framework it is the base command a backend shells out to — e.g.
    # "uv run teacup-agent" for framework: teacup-agent-cli (external_cli.py). Kept
    # deliberately, not deleted: see docs/execution.md item #6.
    entrypoint: str | None
    model_primary: str
    model_fallback: str | None
    instructions_path: str
    tools: tuple[str, ...]
    skills: tuple[str, ...]
    goal_description: str
    goal_checks: tuple[str, ...]
    goal_max_attempts: int
    budget_usd: float | None
    budget_max_tool_calls: int | None
    budget_max_wall_clock_s: float | None
    environment_required: tuple[str, ...]
    derived_from: str | None
    root: Path
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    # -- resources ---------------------------------------------------------

    def read(self, relative: str) -> str:
        """Read a file the manifest points at, relative to the package root."""
        path = self.root / relative
        try:
            return path.read_text(encoding="utf-8")
        except (FileNotFoundError, NotADirectoryError) as exc:
            raise ManifestError(
                f"{MANIFEST_NAME} references {relative!r}, which is missing"
            ) from exc

    def instructions(self) -> str:
        return self.read(self.instructions_path).strip()

    def skill_body(self, skill: str) -> str:
        return strip_frontmatter(self.read(f"skills/{skill}/SKILL.md"))

    def skill_meta(self, skill: str) -> SkillMeta | None:
        """Parsed, spec-validated frontmatter for a packaged skill, or `None` if its
        `SKILL.md` has no `name`/`description`, or its declared `name` isn't spec-shaped
        or disagrees with the folder it lives in — the same conformance rule
        teacup-agent's own `skills.py` applies, so a skill that validates in one repo
        validates in the other."""
        text = self.read(f"skills/{skill}/SKILL.md")
        meta = parse_frontmatter(text)
        name = str(meta.get("name") or skill)
        description = str(meta.get("description", "")).strip()
        if not description:
            return None
        if name != skill or not _SKILL_NAME_RE.match(name) or len(name) > _SKILL_MAX_NAME_LEN:
            return None
        if len(description) > _SKILL_MAX_DESCRIPTION_LEN:
            description = description[:_SKILL_MAX_DESCRIPTION_LEN]
        allowed_tools_raw = meta.get("allowed-tools", "")
        allowed_tools = tuple(str(allowed_tools_raw).split()) if allowed_tools_raw else ()
        metadata = meta.get("metadata") or {}
        return SkillMeta(
            name=name,
            description=description,
            license=meta.get("license"),
            compatibility=meta.get("compatibility"),
            metadata=metadata if isinstance(metadata, dict) else {},
            allowed_tools=allowed_tools,
        )

    def available_skills(self) -> tuple[str, ...]:
        """Packaged skill names whose `SKILL.md` is Agent Skills-conformant. A folder
        with a `SKILL.md` that fails validation (no description, a spec-illegal or
        folder-mismatched name) is not offered — the same "malformed is skipped, not
        fatal" rule `skills.py`'s own `discover()` applies, rather than surfacing a
        skill `add_skill()` would only fail on later."""
        skills_dir = self.root / "skills"
        if not skills_dir.is_dir():
            return ()
        names = sorted(d.name for d in skills_dir.iterdir() if (d / "SKILL.md").is_file())
        return tuple(name for name in names if self.skill_meta(name) is not None)

    # -- loading -----------------------------------------------------------

    @classmethod
    def load(cls, root: str | Path, *, manifest_text: str | None = None) -> "AgentSpec":
        root = Path(root)
        if manifest_text is None:
            path = root / MANIFEST_NAME
            if not path.is_file():
                raise ManifestError(f"no {MANIFEST_NAME} in {root}")
            manifest_text = path.read_text(encoding="utf-8")

        try:
            data = yaml.safe_load(manifest_text)
        except yaml.YAMLError as exc:
            raise ManifestError(f"{MANIFEST_NAME} is not valid YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise ManifestError(f"{MANIFEST_NAME} must be a YAML mapping at the top level")

        def require(key: str) -> Any:
            if not data.get(key):
                raise ManifestError(f"{MANIFEST_NAME} is missing the required key {key!r}")
            return data[key]

        model = data.get("model") or {}
        if not isinstance(model, dict) or not model.get("primary"):
            raise ManifestError(f"{MANIFEST_NAME} must define model.primary")

        goal = data.get("goal") or {}
        raw_attempts = goal.get("max_attempts")
        max_attempts = 1 if raw_attempts is None else _as_int(raw_attempts)
        if max_attempts is None or max_attempts < 1:
            raise ManifestError(f"{MANIFEST_NAME}: goal.max_attempts must be an integer >= 1")

        budget = data.get("budget") or {}
        environment = data.get("environment") or {}
        lineage = data.get("lineage") or {}

        return cls(
            name=str(require("name")),
            version=str(require("version")),
            description=str(data.get("description", "")),
            framework=str(data.get("framework", "teacup")),
            entrypoint=(str(data["entrypoint"]) if data.get("entrypoint") else None),
            model_primary=str(model["primary"]),
            model_fallback=(str(model["fallback"]) if model.get("fallback") else None),
            instructions_path=str(data.get("instructions", "prompts/system.md")),
            tools=tuple(data.get("tools") or ()),
            skills=tuple(data.get("skills") or ()),
            goal_description=str(goal.get("description", "")),
            goal_checks=tuple(goal.get("checks") or ()),
            goal_max_attempts=max_attempts,
            budget_usd=_as_float(budget.get("default_usd")),
            budget_max_tool_calls=_as_int(budget.get("max_tool_calls")),
            budget_max_wall_clock_s=_as_float(budget.get("max_wall_clock_s")),
            environment_required=tuple(environment.get("required") or ()),
            derived_from=(str(lineage["derived_from"]) if lineage.get("derived_from") else None),
            root=root,
            raw=data,
        )

    def validate(self, *, tools: set[str], checks: set[str]) -> None:
        """Cross-check the manifest against what the package actually provides."""
        unknown_tools = sorted(set(self.tools) - tools)
        if unknown_tools:
            raise ManifestError(
                f"{MANIFEST_NAME} declares unknown tools: {', '.join(unknown_tools)}. "
                f"Known tools: {', '.join(sorted(tools)) or 'none'}."
            )
        unknown_checks = sorted(set(self.goal_checks) - checks)
        if unknown_checks:
            raise ManifestError(
                f"{MANIFEST_NAME} declares unknown goal checks: {', '.join(unknown_checks)}. "
                f"Known checks: {', '.join(sorted(checks)) or 'none'}."
            )
        unknown_skills = sorted(set(self.skills) - set(self.available_skills()))
        if unknown_skills:
            raise ManifestError(
                f"{MANIFEST_NAME} declares skills with no SKILL.md: {', '.join(unknown_skills)}"
            )
        self.instructions()  # raises if the prompt file is missing

    def to_dict(self) -> dict[str, Any]:
        """The manifest as it should be written back out (used when publishing)."""
        return dict(self.raw)


def strip_frontmatter(text: str) -> str:
    """Drop a leading `---`-delimited YAML block, if present."""
    if not text.startswith("---"):
        return text.strip()
    end = text.find("\n---", 3)
    if end == -1:
        return text.strip()
    return text[end + 4 :].strip()


def parse_frontmatter(text: str) -> dict[str, Any]:
    """Read a leading `---`-delimited YAML block. Returns {} when absent."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    parsed = yaml.safe_load(text[3:end])
    return parsed if isinstance(parsed, dict) else {}


def _as_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _as_int(value: Any) -> int | None:
    return None if value is None else int(value)
