"""`AutoAgent` — the four lines from the README.

    agent = AutoAgent.from_pretrained("alice/deep-research")
    agent.add_skill("pdf-analysis")
    agent.set_budget(5)
    result = agent.run("Research this company")
    agent.push_to_hub("ray/deep-research-plus")

The class is a thin facade: it resolves a reference to a directory, loads the
manifest, imports that package's tools and checks, and hands the loop what it
needs. Everything it does is available underneath if you would rather call
`teacup_run.run()` yourself.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from . import registry
from .budget import Budget
from .goal import BUILTIN_CHECKS, Check
from .loop import Result, run
from .manifest import AgentSpec, ManifestError, strip_frontmatter
from .model import Reply, call_model
from .tools import Tool

__all__ = ["AutoAgent"]


@dataclass
class AutoAgent:
    """A loaded agent: its manifest, its tools, its checks, and its overrides."""

    spec: AgentSpec
    tools: list[Tool] = field(default_factory=list)
    checks: dict[str, Check] = field(default_factory=dict)
    model: str = ""
    budget: Budget | None = None
    extra_instructions: list[str] = field(default_factory=list)
    enabled_skills: list[str] = field(default_factory=list)

    # -- loading -----------------------------------------------------------

    @classmethod
    def from_pretrained(cls, ref: str, *, hub: Path | None = None) -> "AutoAgent":
        """Load an agent from a local path, the hub, or a git URL."""
        root = registry.resolve(ref, hub=hub)
        spec = AgentSpec.load(root)

        tools = _load_tools(root)
        checks = {**BUILTIN_CHECKS, **_load_checks(root)}
        spec.validate(tools=set(tools), checks=set(checks))

        agent = cls(
            spec=spec,
            tools=[tools[name] for name in spec.tools],
            checks=checks,
            model=spec.model_primary,
            budget=Budget(
                usd=spec.budget_usd,
                max_tool_calls=spec.budget_max_tool_calls,
                deadline_s=spec.budget_max_wall_clock_s,
            ),
        )
        for skill in spec.skills:
            agent.add_skill(skill)
        return agent

    # -- the extension surface --------------------------------------------

    def add_skill(self, skill: str | Path) -> "AutoAgent":
        """Enable a packaged skill by name, or load one from a directory."""
        if isinstance(skill, Path) or "/" in str(skill) or "\\" in str(skill):
            path = Path(skill) / "SKILL.md"
            if not path.is_file():
                raise ManifestError(f"no SKILL.md at {path}")
            self.extra_instructions.append(strip_frontmatter(path.read_text(encoding="utf-8")))
            self.enabled_skills.append(Path(skill).name)
            return self

        if skill in self.enabled_skills:
            return self
        available = self.spec.available_skills()
        if skill not in available:
            raise ManifestError(
                f"no skill {skill!r} in this package. Available: {', '.join(available) or 'none'}."
            )
        self.extra_instructions.append(f"# Skill: {skill}\n\n{self.spec.skill_body(skill)}")
        self.enabled_skills.append(skill)
        return self

    def add_tool(self, new_tool: Tool) -> "AutoAgent":
        self.tools.append(new_tool)
        return self

    def set_model(self, model: str) -> "AutoAgent":
        self.model = model
        return self

    def set_budget(self, budget: Budget | float | int) -> "AutoAgent":
        self.budget = Budget.of(budget)
        return self

    def extend_instructions(self, text: str) -> "AutoAgent":
        self.extra_instructions.append(text.strip())
        return self

    # -- running -----------------------------------------------------------

    def instructions(self) -> str:
        parts = [self.spec.instructions(), *self.extra_instructions]
        return "\n\n---\n\n".join(p.strip() for p in parts if p.strip())

    def run(
        self,
        task: str,
        *,
        budget: Budget | float | None = None,
        goal_loop: bool = True,
        artifacts: dict[str, Any] | None = None,
        model_fn: Callable[..., Reply] = call_model,
    ) -> Result:
        """Run one task under a budget, retrying while the goal is unmet."""
        return run(
            task,
            model=self.model or self.spec.model_primary,
            instructions=self.instructions(),
            tools=self.tools,
            budget=Budget.of(budget) if budget is not None else (self.budget or Budget()),
            checks=self.checks,
            goal_checks=self.spec.goal_checks if goal_loop else (),
            goal_description=self.spec.goal_description,
            max_attempts=self.spec.goal_max_attempts,
            artifacts=artifacts,
            model_fn=model_fn,
        )

    def eval(
        self,
        *,
        budget: Budget | float | None = None,
        benchmark: str | Path | None = None,
        compare: str | None = None,
        model_fn: Callable[..., Reply] = call_model,
    ):
        """Run this agent's benchmark and report quality *and* cost."""
        from .evaluate import load_benchmark, run_benchmark

        suite = load_benchmark(benchmark or _default_benchmark(self.spec.root))
        return run_benchmark(
            self,
            suite,
            budget=Budget.of(budget) if budget is not None else (self.budget or Budget()),
            compare=compare,
            model_fn=model_fn,
        )

    # -- publishing --------------------------------------------------------

    def push_to_hub(
        self, ref: str, *, hub: Path | None = None, version: str | None = None
    ) -> Path:
        """Publish this agent under `ref`, recording what it was derived from."""
        self.spec.validate(tools={t.name for t in self.tools}, checks=set(self.checks))

        manifest = self.spec.to_dict()
        manifest["name"] = ref
        manifest["version"] = version or _bump(self.spec.version)
        manifest["lineage"] = {"derived_from": self.spec.name}
        if self.model and self.model != self.spec.model_primary:
            manifest.setdefault("model", {})["primary"] = self.model
        if self.enabled_skills:
            manifest["skills"] = sorted(set(self.enabled_skills))

        target = registry.publish(self.spec.root, ref, hub=hub, message=f"Publish {ref}")
        (target / "agent.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        return target


# -- package loading -------------------------------------------------------


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - unreachable for real files
        raise ManifestError(f"could not import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_tools(root: Path) -> dict[str, Tool]:
    """Collect `@tool` functions from the package's tools.py or tools/ package."""
    path = root / "tools.py"
    if not path.is_file():
        path = root / "tools" / "__init__.py"
    if not path.is_file():
        return {}
    module = _load_module(path, f"teacup_run_pkg_{root.name}_tools")
    return {
        value.name: value
        for value in vars(module).values()
        if isinstance(value, Tool)
    }


def _load_checks(root: Path) -> dict[str, Check]:
    """Collect `@check` functions from the package's checks.py."""
    path = root / "checks.py"
    if not path.is_file():
        return {}
    module = _load_module(path, f"teacup_run_pkg_{root.name}_checks")
    return {
        name: value
        for name, value in vars(module).items()
        if callable(value) and getattr(value, "is_teacup_run_check", False)
    }


def _default_benchmark(root: Path) -> Path:
    """`evals/` lives in the agent directory, so publishing carries it along.

    That is what makes `from_pretrained` -> `eval` work for a pulled agent: the
    benchmark travelled with it, and a fork can be measured against upstream.
    """
    candidate = root / "evals" / "benchmark.yaml"
    if candidate.is_file():
        return candidate
    raise ManifestError(
        f"no evals/benchmark.yaml found for {root.name}. Pass benchmark=<path> to eval()."
    )


def _bump(version: str) -> str:
    """Bump the last numeric component: 0.1.0 -> 0.1.1."""
    parts = version.split(".")
    if parts and parts[-1].isdigit():
        parts[-1] = str(int(parts[-1]) + 1)
        return ".".join(parts)
    return f"{version}+1"
