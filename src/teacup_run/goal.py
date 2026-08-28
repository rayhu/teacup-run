"""The stop condition a framework will not give you.

An agent loop ends when the model stops calling tools. Nothing in that asks
whether the work is any good. A goal is that missing predicate: checks over what
the run produced, evaluated in code, cheap and deterministic.

A check returns `""` when it passes, or a sentence describing the fix when it
fails. Phrasing failures as instructions is what makes another attempt worth
paying for.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

__all__ = [
    "Attempt",
    "GoalVerdict",
    "BUILTIN_CHECKS",
    "check",
    "evaluate",
    "revision_prompt",
]


@dataclass(frozen=True)
class Attempt:
    """What a check gets to look at."""

    answer: str
    artifacts: dict[str, Any] = field(default_factory=dict)
    tool_calls: tuple[str, ...] = ()


Check = Callable[[Attempt], str]

BUILTIN_CHECKS: dict[str, Check] = {}


def check(fn: Check) -> Check:
    """Register a check under its function name.

    Used by the library for its builtins and by an agent package's own
    `checks.py`, so a manifest can name either.
    """
    fn.is_teacup_run_check = True  # type: ignore[attr-defined]
    return fn


def _builtin(fn: Check) -> Check:
    BUILTIN_CHECKS[fn.__name__] = check(fn)
    return fn


@_builtin
def non_empty(attempt: Attempt) -> str:
    """The answer says something."""
    if attempt.answer.strip():
        return ""
    return "The answer is empty. Write the answer itself, not a description of it."


@_builtin
def no_question_back(attempt: Attempt) -> str:
    """The answer answers rather than asking the user what to do."""
    tail = attempt.answer.strip()[-200:]
    if re.search(r"(which do you want|shall i|would you like me to|let me know).{0,40}\?", tail, re.I):
        return (
            "The answer ends by asking the user what to do. Decide, and give the best "
            "answer you can with what you have."
        )
    return ""


@_builtin
def used_a_tool(attempt: Attempt) -> str:
    """The agent actually did the work rather than answering from memory."""
    if attempt.tool_calls:
        return ""
    return "You answered without calling any tool. Use the tools available to you first."


@dataclass(frozen=True)
class GoalVerdict:
    """Whether an attempt met the goal, and what to fix if it did not."""

    met: bool
    checks: dict[str, bool]
    reasons: tuple[str, ...] = ()

    @property
    def passed(self) -> int:
        return sum(1 for ok in self.checks.values() if ok)

    @property
    def failed(self) -> tuple[str, ...]:
        return tuple(name for name, ok in self.checks.items() if not ok)

    def summary(self) -> str:
        total = len(self.checks)
        if self.met:
            return f"goal met ({total}/{total} checks)"
        return (
            f"goal not met ({self.passed}/{total} checks; failed: {', '.join(self.failed)})"
        )


def evaluate(checks: dict[str, Check], names: Sequence[str], attempt: Attempt) -> GoalVerdict:
    """Run the named checks over one attempt."""
    results: dict[str, bool] = {}
    reasons: list[str] = []
    for name in names:
        reason = checks[name](attempt)
        results[name] = not reason
        if reason:
            reasons.append(reason)
    return GoalVerdict(met=not reasons, checks=results, reasons=tuple(reasons))


def revision_prompt(
    task: str, description: str, answer: str, verdict: GoalVerdict, attempt: int, max_attempts: int
) -> str:
    """The next attempt's input: the goal, the gap, and what was already written.

    The previous answer is included so the model revises rather than restarts —
    a retry that discards good work is how a goal loop loses to no loop at all.
    """
    gaps = "\n".join(f"- {reason}" for reason in verdict.reasons)
    return (
        f"Your previous answer did not meet the goal for this task.\n\n"
        f"Goal: {description}\n\n"
        f"What is missing:\n{gaps}\n\n"
        f"Original task: {task}\n\n"
        f"Your previous answer:\n{answer}\n\n"
        f"This is attempt {attempt + 1} of {max_attempts}. Fix the gaps above and write "
        f"the answer again in full. Keep what was already right: this is a revision, not "
        f"a restart, and a revision that loses substance is worse than the answer it "
        f"replaced. Never reply with a question to the user, and do not restate this "
        f"instruction in the answer."
    )
