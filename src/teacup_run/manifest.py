"""`agent.yaml` — the package's contract.

Validation is deliberate: a manifest naming a tool the package does not
implement, or a prompt file that is not there, should fail with a sentence a
human can act on, not a KeyError from inside a run.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import env as env_mod

__all__ = [
    "AgentSpec",
    "AGENTS_MD_NAME",
    "ManifestError",
    "MANIFEST_NAME",
    "SkillMeta",
    "parse_frontmatter",
    "strip_frontmatter",
]

MANIFEST_NAME = "agent.yaml"

# Every value routes somewhere: "teacup" to the native loop, anything else to
# `external_cli.run_external`, which splits the manifest's `entrypoint:` and executes
# it. An unrecognised value therefore does not fail — it shells out. That makes a typo
# (`framework: teacup-agent`) silently launch a subprocess instead of erroring, so the
# set is closed. It is a correctness guard, not a security boundary: a hostile package
# just writes one of these two names. See roadmap item 2.
KNOWN_FRAMEWORKS = ("teacup", "teacup-agent-cli")
AGENTS_MD_NAME = "AGENTS.md"

# Agent Skills spec (platform.claude.com/docs/en/agents-and-tools/agent-skills):
# name is lowercase letters, digits and hyphens, max 64 chars, must not contain the
# reserved words "claude"/"anthropic", and (teacup's own added rule, applied
# consistently by both repos) must equal the skill's folder name — the same checks
# teacup-agent's own skills.py enforces, so a skill package validated by either repo
# means the same thing. description must be non-empty and is capped at 1024 chars:
# one catalog line, not a paragraph.
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_SKILL_MAX_NAME_LEN = 64
_SKILL_MAX_DESCRIPTION_LEN = 1024
_SKILL_RESERVED_NAME_WORDS = ("claude", "anthropic")


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

    def _inside(self, relative: str) -> Path:
        """Resolve a manifest-declared path, refusing to leave the package.

        Every path in `agent.yaml` is written by whoever wrote the package, and
        running a package you did not write is the entire point of the hub — so
        `instructions: ../../.env` is not a hypothetical. That file is read into the
        system prompt and sent to the provider, and `validate()` reads it during
        `--dry-run`, the mode a cautious user runs *instead of* committing to the
        package. So the check belongs here, at the resolve, rather than at each of
        the three call sites that would each have to remember it.

        `.resolve()` on both sides so a symlink planted inside the package is caught
        too, not just a `..` in the manifest text.

        This covers the paths the schema itself defines. It does not cover
        `teacup_agent.project_root`, which is framework-specific, escapes the package
        by design (the bridge points at a sibling checkout), and is part of the larger
        "what may a foreign package do" question — roadmap item 2, not this function.
        """
        root = self.root.resolve()
        path = (root / relative).resolve()
        if path != root and root not in path.parents:
            raise ManifestError(
                f"refusing to read {relative!r}: it resolves outside the package "
                f"({root}). A package may only read its own files."
            )
        return path

    def read(self, relative: str) -> str:
        """Read a file the manifest points at, relative to the package root."""
        path = self._inside(relative)
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
        path = self._inside(AGENTS_MD_NAME)
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
        own_path = self._inside(self.instructions_path)
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

    def skill_meta(self, skill: str) -> SkillMeta | None:
        """Parsed, spec-validated frontmatter for a packaged skill, or `None` if its
        `SKILL.md` has no `name`/`description`/body, its declared `name` isn't
        spec-shaped, contains a reserved word, or disagrees with the folder it lives
        in — the same conformance rule teacup-agent's own `skills.py` applies (body
        included: a skill with no procedure to load is exactly as unusable as one
        with no description), so a skill that validates in one repo validates in the
        other."""
        try:
            text = self.read(f"skills/{skill}/SKILL.md")
        except ManifestError:
            # Skipped, not fatal — which is what `available_skills` documents and what
            # `skills.py`'s `discover()` does. A `skills/<name>` directory that is a
            # symlink out of the package is refused by `_inside`, and raising here made
            # `validate()` — and so the whole package — unloadable over one skill that
            # simply should not be offered.
            return None
        meta = parse_frontmatter(text)
        name = str(meta.get("name") or skill)
        description = str(meta.get("description", "")).strip()
        body = strip_frontmatter(text)
        if not description or not body:
            return None
        if (
            name != skill
            or not _SKILL_NAME_RE.match(name)
            or len(name) > _SKILL_MAX_NAME_LEN
            or any(word in name for word in _SKILL_RESERVED_NAME_WORDS)
        ):
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

    def _skill_folders(self) -> tuple[str, ...]:
        """Folder names under `skills/` that have a `SKILL.md` file — valid or not.
        Kept separate from `available_skills()` so `validate()` can tell "no SKILL.md
        at all" apart from "a SKILL.md that fails Agent Skills validation" instead of
        reporting both as the same misleading "no SKILL.md" error."""
        skills_dir = self.root / "skills"
        if not skills_dir.is_dir():
            return ()
        return tuple(sorted(d.name for d in skills_dir.iterdir() if (d / "SKILL.md").is_file()))

    def available_skills(self) -> tuple[str, ...]:
        """Packaged skill names whose `SKILL.md` is Agent Skills-conformant. A folder
        with a `SKILL.md` that fails validation (no description/body, a spec-illegal,
        reserved, or folder-mismatched name) is not offered — the same "malformed is
        skipped, not fatal" rule `skills.py`'s own `discover()` applies, rather than
        surfacing a skill `add_skill()` would only fail on later."""
        return tuple(name for name in self._skill_folders() if self.skill_meta(name) is not None)

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
            framework=_framework(data.get("framework", "teacup")),
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

    def missing_environment(self) -> tuple[str, ...]:
        """Declared environment variables that are absent or still placeholders.

        Deliberately **not** part of `validate()`. That runs inside
        `from_pretrained()`, and this repo's own suite loads `examples/note-taker`
        with a faked model and no key at all — enforcing there would make the library
        unusable offline, which is the opposite of what a preflight check is for.
        The CLI asks this question separately, once, before it spends anything.

        A placeholder counts as missing for the same reason `env.py` refuses to load
        one: a copied-but-unedited `.env` produces a 401 halfway through a run rather
        than a message up front, and the second is the one worth having.
        """
        missing = []
        for name in self.environment_required:
            # Stripped the same way env.py strips a value it loads, quotes included:
            # an exported OPENAI_API_KEY='sk-...' must not be a placeholder to one
            # of them and a real key to the other.
            value = os.environ.get(name, "").strip().strip("'\"")
            if not value or value.lower() in env_mod.PLACEHOLDERS:
                missing.append(name)
        return tuple(missing)

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
        skill_folders = set(self._skill_folders())
        missing_skills = sorted(set(self.skills) - skill_folders)
        if missing_skills:
            raise ManifestError(
                f"{MANIFEST_NAME} declares skills with no SKILL.md: {', '.join(missing_skills)}"
            )
        # Distinct from the missing-file case above: the SKILL.md exists but its
        # frontmatter fails Agent Skills validation (bad name, no description/body).
        # Reporting this as "no SKILL.md" (as an earlier version of this check did)
        # sends a human looking for a file that is right there.
        invalid_skills = sorted(name for name in self.skills if self.skill_meta(name) is None)
        if invalid_skills:
            raise ManifestError(
                f"{MANIFEST_NAME} declares skills whose SKILL.md fails Agent Skills "
                f"validation (bad name, or no description/body): {', '.join(invalid_skills)}"
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


def _framework(value: Any) -> str:
    name = str(value)
    if name not in KNOWN_FRAMEWORKS:
        raise ManifestError(
            f"{MANIFEST_NAME} declares framework {name!r}, which this version does not "
            f"know how to run. Known frameworks: {', '.join(KNOWN_FRAMEWORKS)}."
        )
    return name


def _as_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _as_int(value: Any) -> int | None:
    return None if value is None else int(value)
