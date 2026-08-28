"""Budgeted evaluation: quality *and* cost, never one without the other.

A single score cannot rank agents, because the ranking at $1 is not the ranking
at $10. Every row here carries what it achieved and what it spent, and the
optional two-arm comparison exists because "is the goal loop better?" is not
answerable without "better per dollar?".

Scoring is keyword-and-goal based and deliberately crude: a library should not
force an LLM-judge dependency on someone who just wants to run their benchmark.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from .budget import Budget
from .model import Reply, call_model

__all__ = ["Report", "Row", "load_benchmark", "run_benchmark", "score"]


@dataclass
class Row:
    task_id: str
    arm: str
    quality: float
    cost: float
    attempts: int
    goal_met: bool | None
    keyword_hits: int
    keyword_total: int
    stopped_early: bool


@dataclass
class Report:
    rows: list[Row] = field(default_factory=list)
    threshold: float = 0.5
    arms: tuple[str, ...] = ("default",)

    def for_arm(self, arm: str) -> list[Row]:
        return [r for r in self.rows if r.arm == arm]

    def summary(self, arm: str) -> dict[str, float]:
        rows = self.for_arm(arm)
        if not rows:
            return {}
        quality = statistics.fmean(r.quality for r in rows)
        cost = statistics.fmean(r.cost for r in rows)
        return {
            "success": sum(1 for r in rows if r.quality >= self.threshold) / len(rows),
            "quality": quality,
            "cost": cost,
            "quality_per_dollar": quality / cost if cost else float("inf"),
            "attempts": statistics.fmean(r.attempts for r in rows),
        }

    def render(self) -> str:
        out: list[str] = []
        for arm in self.arms:
            out.append(f"── {arm}" if len(self.arms) > 1 else "── results")
            for row in self.for_arm(arm):
                met = {None: "-", True: "met", False: "UNMET"}[row.goal_met]
                out.append(
                    f"  {row.task_id:<24} quality {row.quality:.2f}  cost ${row.cost:.4f}  "
                    f"attempts {row.attempts}  goal {met:<5} "
                    f"kw {row.keyword_hits}/{row.keyword_total}"
                    + ("  (stopped early)" if row.stopped_early else "")
                )
            out.append("")

        out.append(f"{'Arm':<12}{'Success':>9}{'Quality':>10}{'Avg. Cost':>12}{'Quality/$':>12}{'Attempts':>10}")
        out.append("─" * 65)
        for arm in self.arms:
            s = self.summary(arm)
            if not s:
                continue
            out.append(
                f"{arm:<12}{s['success']:>8.0%}{s['quality']:>10.2f}{s['cost']:>12.4f}"
                f"{s['quality_per_dollar']:>12,.1f}{s['attempts']:>10.1f}"
            )
        out.append("")
        out.append(f"success = quality >= {self.threshold:.2f}")
        return "\n".join(out)

    def __str__(self) -> str:
        return self.render()


def load_benchmark(path: str | Path) -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data.get("tasks"):
        raise ValueError(f"{path} has no tasks")
    return data


def score(answer: str, goal_met: bool | None, task: dict[str, Any], cfg: dict[str, Any]) -> tuple[float, int, int]:
    """Keyword coverage, plus credit for meeting the agent's own goal."""
    keywords = [str(k).lower() for k in task.get("rubric_keywords", [])]
    lowered = answer.lower()
    hits = sum(1 for k in keywords if k in lowered)
    keyword_score = hits / len(keywords) if keywords else 0.0

    kw = float(cfg.get("keyword_weight", 0.7))
    gw = float(cfg.get("goal_weight", 0.3))
    goal_score = 1.0 if goal_met else 0.0
    quality = (keyword_score * kw + goal_score * gw) / (kw + gw)
    return quality, hits, len(keywords)


def run_benchmark(
    agent,
    suite: dict[str, Any],
    *,
    budget: Budget | float | None = None,
    compare: str | None = None,
    repeat: int = 1,
    model_fn: Callable[..., Reply] = call_model,
) -> Report:
    """Run every task, optionally twice — with and without the goal loop.

    Args:
        compare: "goal_loop" runs both arms. Anything else runs one.
    """
    cfg = suite.get("scoring", {})
    arms = ("baseline", "goal-loop") if compare == "goal_loop" else ("default",)
    report = Report(threshold=float(cfg.get("success_threshold", 0.5)), arms=arms)

    for task in suite["tasks"]:
        for _ in range(repeat):
            for arm in arms:
                result = agent.run(
                    task["question"],
                    budget=budget,
                    goal_loop=arm != "baseline",
                    model_fn=model_fn,
                )
                goal_met = None if result.goal is None else result.goal.met
                quality, hits, total = score(result.answer, goal_met, task, cfg)
                report.rows.append(
                    Row(
                        task_id=task["id"],
                        arm=arm,
                        quality=quality,
                        cost=result.ledger.total_cost,
                        attempts=result.attempts,
                        goal_met=goal_met,
                        keyword_hits=hits,
                        keyword_total=total,
                        stopped_early=result.stopped_early,
                    )
                )
    return report
