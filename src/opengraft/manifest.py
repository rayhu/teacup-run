"""`agent.yaml` — the package's contract.

Validation is deliberate: a manifest naming a tool the package does not
implement, or a prompt file that is not there, should fail with a sentence a
human can act on, not a KeyError from inside a run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

__all__ = ["AgentSpec", "ManifestError", "MANIFEST_NAME", "parse_frontmatter", "strip_frontmatter"]

MANIFEST_NAME = "agent.yaml"


class ManifestError(ValueError):
    """The manifest is missing, malformed, or inconsistent with the package."""


@dataclass
class AgentSpec:
    """A parsed manifest, plus where it was read from."""

    name: str
    version: str
    description: str
    framework: str
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

    def available_skills(self) -> tuple[str, ...]:
        skills_dir = self.root / "skills"
        if not skills_dir.is_dir():
            return ()
        return tuple(sorted(d.name for d in skills_dir.iterdir() if (d / "SKILL.md").is_file()))

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
            framework=str(data.get("framework", "opengraft")),
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
