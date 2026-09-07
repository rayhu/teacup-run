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

__all__ = [
    "AgentSpec",
    "AGENTS_MD_NAME",
    "ManifestError",
    "MANIFEST_NAME",
    "parse_frontmatter",
    "strip_frontmatter",
]

MANIFEST_NAME = "agent.yaml"
AGENTS_MD_NAME = "AGENTS.md"


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

    def agents_md(self) -> str | None:
        """The package's own `AGENTS.md`, if it has one — the open, now
        Linux-Foundation-governed convention (github.com/agentsmd/agents.md) a
        repo-root instructions file for a coding agent already follows across 20+
        tools. Plain markdown, no frontmatter, so unlike a skill there is nothing to
        validate — its presence is the whole contract.

        Scoped to the package's own root only for now, not the full spec's nested
        walk (an `AGENTS.md` in every parent directory up to a repo root, closest
        wins on conflict) — that needs a repo-boundary heuristic this format doesn't
        have a natural one for yet, and reading arbitrary ancestor directories by
        default is exactly the kind of scope creep this project's own threat model
        should decide on deliberately, not acquire by accident.
        """
        path = self.root / AGENTS_MD_NAME
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8").strip()

    def instructions(self) -> str:
        """The instructions a run's system prompt is built from.

        Combines two independent sources: the package's own `AGENTS.md`, if it has
        one, as background context, followed by its own authored instructions file
        (`instructions:` in `agent.yaml`, default `prompts/system.md`) as the actual
        task-specific persona — unless that file doesn't exist, in which case
        `AGENTS.md` alone stands in for it, so a plain `AGENTS.md`-only directory
        works as a Teacup Run package without also requiring Teacup's own file on
        top of a convention that already covers the same ground.
        """
        own_path = self.root / self.instructions_path
        own = own_path.read_text(encoding="utf-8").strip() if own_path.is_file() else None
        agents = self.agents_md()

        if own is None and agents is None:
            raise ManifestError(
                f"{MANIFEST_NAME} references {self.instructions_path!r}, which is missing, "
                f"and there is no {AGENTS_MD_NAME} to fall back to"
            )
        if own is None:
            return agents
        if agents is None:
            return own
        return f"{agents}\n\n---\n\n{own}"

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
        self.instructions()  # raises if neither the prompt file nor AGENTS.md exists

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
