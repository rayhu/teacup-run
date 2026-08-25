"""The agent loop, and the goal loop around it.

Two loops, and the difference matters:

- The **inner** loop is what every framework gives you: call the model, run the
  tools it asked for, feed the results back, repeat until it stops calling tools.
- The **outer** loop is what none of them give you: check whether the result
  actually met the goal, and if not, feed the gap back and try again.

Two details here are not decoration. Both were measured:

1. The budget is checked *before* each call. Checked afterwards, a ceiling is an
   invoice for a run you did not want.
2. The loop keeps the **best** attempt, not the last. Fed its own failures, a
   model can talk itself into a worse answer; keeping the last attempt made a
   goal loop lose to no loop at all. Retrying should only cost money, never
   quality.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .budget import Budget, BudgetExceeded, Ledger
from .goal import Attempt, Check, GoalVerdict, evaluate, revision_prompt
from .model import Reply, call_model
from .tools import Tool, dispatch

__all__ = ["Result", "run"]

MAX_TURNS = 12


@dataclass
class Result:
    """What a run produced, and what it cost."""

    answer: str
    ledger: Ledger
    attempts: int = 1
    goal: GoalVerdict | None = None
    artifacts: dict[str, Any] = field(default_factory=dict)
    tool_calls: tuple[str, ...] = ()
    stopped_early: bool = False
    stop_reason: str | None = None

    def render_ledger(self, budget: Budget | None = None) -> str:
        header = "Task stopped early" if self.stopped_early else "Task completed"
        return self.ledger.render(budget=budget, header=header)

    def __str__(self) -> str:
        return self.answer


def run(
    task: str,
    *,
    model: str,
    instructions: str,
    tools: Sequence[Tool] = (),
    budget: Budget | float | None = None,
    checks: Mapping[str, Check] | None = None,
    goal_checks: Sequence[str] = (),
    goal_description: str = "",
    max_attempts: int = 1,
    artifacts: dict[str, Any] | None = None,
    max_turns: int = MAX_TURNS,
    model_fn: Callable[..., Reply] = call_model,
) -> Result:
    """Run one task to a goal, under a budget.

    Args:
        artifacts: Shared state the tools write to and the checks read. It
            persists across attempts, so a retry builds on what was gathered.
        model_fn: The seam tests replace. Defaults to the real provider call.
    """
    budget = Budget.of(budget)
    ledger = Ledger()
    by_name = {t.name: t for t in tools}
    schemas = [t.schema() for t in tools]
    artifacts = {} if artifacts is None else artifacts
    checks = dict(checks or {})

    called: list[str] = []
    best: tuple[tuple[int, int], str, GoalVerdict | None] | None = None
    verdict: GoalVerdict | None = None
    stopped_early = False
    stop_reason: str | None = None
    answer = ""
    attempts = 0
    prompt = task

    while True:
        attempts += 1
        try:
            answer = _one_attempt(
                prompt,
                model=model,
                instructions=instructions,
                schemas=schemas,
                by_name=by_name,
                artifacts=artifacts,
                ledger=ledger,
                budget=budget,
                called=called,
                max_turns=max_turns,
                model_fn=model_fn,
            )
        except BudgetExceeded as exc:
            stopped_early, stop_reason = True, exc.reason
            answer = _fallback(best, answer, f"The run stopped before finishing: {exc.reason}")
            break
        except Exception as exc:  # noqa: BLE001 - surfaced with the ledger, not swallowed
            stopped_early, stop_reason = True, f"{type(exc).__name__}: {exc}"
            answer = _fallback(best, answer, f"The run failed: {stop_reason}")
            break

        if not goal_checks:
            break

        attempt = Attempt(answer=answer, artifacts=artifacts, tool_calls=tuple(called))
        verdict = evaluate(checks, goal_checks, attempt)
        rank = (verdict.passed, len(answer.strip()))
        if best is None or rank > best[0]:
            best = (rank, answer, verdict)

        if verdict.met or attempts >= max_attempts:
            answer, verdict = best[1], best[2]
            break

        prompt = revision_prompt(
            task, goal_description, answer, verdict, attempts, max_attempts
        )

    ledger.stop_clock()
    return Result(
        answer=answer,
        ledger=ledger,
        attempts=attempts,
        goal=verdict,
        artifacts=artifacts,
        tool_calls=tuple(called),
        stopped_early=stopped_early,
        stop_reason=stop_reason,
    )


def _one_attempt(
    prompt: str,
    *,
    model: str,
    instructions: str,
    schemas: list[dict[str, Any]],
    by_name: dict[str, Tool],
    artifacts: dict[str, Any],
    ledger: Ledger,
    budget: Budget,
    called: list[str],
    max_turns: int,
    model_fn: Callable[..., Reply],
) -> str:
    """The inner loop: model, tools, model, ... until it answers."""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": instructions},
        {"role": "user", "content": prompt},
    ]

    for _ in range(max_turns):
        budget.check(ledger)
        reply = model_fn(model, messages, schemas)
        ledger.record_model_call(
            model,
            reply.usage.input_tokens,
            reply.usage.output_tokens,
            reply.usage.cached_input_tokens,
        )

        if not reply.tool_calls:
            return reply.text

        messages.append(_assistant_message(reply))
        for call in reply.tool_calls:
            budget.check(ledger)
            output = dispatch(by_name, call.name, call.arguments, artifacts)
            ledger.record_tool_call(call.name)
            called.append(call.name)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": output,
                }
            )

    return reply.text or "The run hit its turn limit before producing an answer."


def _assistant_message(reply: Reply) -> dict[str, Any]:
    import json

    return {
        "role": "assistant",
        "content": reply.text or None,
        # Provider-native payload, replayed verbatim by whichever branch needs
        # it; stripped before any request goes out. See model.py.
        "_raw": reply.raw_message,
        "tool_calls": [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
            }
            for c in reply.tool_calls
        ],
    }


def _fallback(
    best: tuple[tuple[int, int], str, GoalVerdict | None] | None, answer: str, message: str
) -> str:
    """Never lose a usable answer to a failed retry."""
    if best is not None and best[1].strip():
        return best[1]
    return answer.strip() or message
