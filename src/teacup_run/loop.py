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
    # Why it stopped, as a value rather than as prose. `stop_reason` is written for a
    # human and its two producers are a BudgetExceeded message and an f-string of an
    # exception; a caller that needs to exit 2 for one and 3 for the other would have
    # to parse that, which is a smell and would break the moment the wording changed.
    stop_kind: str | None = None  # "budget" | "error" | None

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
    stop_kind: str | None = None
    answer = ""
    attempts = 0
    prompt = task

    while True:
        attempts += 1
        try:
            answer, hit_turn_limit = _one_attempt(
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
            if not goal_checks:
                # Nothing to judge it by, so the limit is the only signal there is that
                # this run never answered — and reporting it as a completed run made
                # `teacup run` exit 0, telling CI a run that ran out of turns succeeded.
                if hit_turn_limit:
                    stopped_early, stop_kind = True, "budget"
                    stop_reason = _turn_limit_reason(max_turns)
                break

            # Inside the try, not after it. A check is ordinary Python written by
            # whoever wrote the package, so it can raise — and when it did, the
            # exception escaped `run()` entirely, taking the ledger with it. The model
            # call had already been paid for; the caller got a traceback and no record
            # of what it cost. "Cost is reported with the result, never separately."
            attempt = Attempt(answer=answer, artifacts=artifacts, tool_calls=tuple(called))
            verdict = evaluate(checks, goal_checks, attempt)
            rank = (verdict.passed, len(answer.strip()))
            if best is None or rank > best[0]:
                best = (rank, answer, verdict)

            if verdict.met or attempts >= max_attempts:
                answer, verdict = best[1], best[2]
                # A ceiling only matters when the goal was not reached: a run that hit
                # its last turn *and* passed its checks did the job, and calling that
                # an early stop would exit 2 for a success. The verdict is evaluated
                # either way, which is what keeps benchmark scores where they were.
                if hit_turn_limit and not (verdict and verdict.met):
                    stopped_early, stop_kind = True, "budget"
                    stop_reason = _turn_limit_reason(max_turns)
                break

            prompt = revision_prompt(
                task, goal_description, answer, verdict, attempts, max_attempts
            )
        except BudgetExceeded as exc:
            stopped_early, stop_reason, stop_kind = True, exc.reason, "budget"
            answer = _fallback(best, answer, f"The run stopped before finishing: {exc.reason}")
            break
        except Exception as exc:  # noqa: BLE001 - surfaced with the ledger, not swallowed
            stopped_early, stop_reason, stop_kind = True, f"{type(exc).__name__}: {exc}", "error"
            # Deliberately dropped: if the failure came out of `evaluate`, `verdict`
            # still holds the *previous* attempt's verdict, and reporting that beside a
            # crash is how a failed run gets to claim its goal was met.
            verdict = None
            answer = _fallback(best, answer, f"The run failed: {stop_reason}")
            break

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
        stop_kind=stop_kind,
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
) -> tuple[str, bool]:
    """The inner loop: model, tools, model, ... until it answers.

    Returns the answer and whether the turn limit ended it rather than the model."""
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
            return reply.text, False

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

    # A ceiling, so it raises like the other ceilings rather than returning.
    #
    # Reaching here means the model was *still calling tools* on the last allowed turn:
    # the loop returns the moment a reply has none. So the run did not finish, and
    # returning its text as an ordinary answer made `stopped_early` False, which the CLI
    # reports as exit 0 — a run that ran out of turns telling CI it succeeded. That is
    # the same defect as an unclassified early stop, one layer down.
    #
    # `BudgetExceeded` and not a new exception type, because §5 has exactly one code for
    # "ran out of an allowance you set" (2) and this is one: `max_tool_calls` and
    # `deadline_s` already come through here. It also means the external backend's
    # `max_steps` — the same concept in the child — can map to the same kind.
    # Reported, not raised.
    #
    # Raising here was wrong in a way that took a second review to see: it skipped
    # `evaluate()` further down, so `Result.goal` came back None, `evaluate.py` scored
    # `goal_met=None` as 0, and a benchmark task that exhausted its turns but *passed
    # its checks* dropped 0.3 of its quality score — enough to cross the 0.5 success
    # threshold. Worse, only the `goal-loop` arm has checks, so `compare="goal_loop"`
    # became biased against the thing it exists to measure.
    #
    # The caller decides what the limit means, once the verdict is in.
    return reply.text, True


def _turn_limit_reason(max_turns: int) -> str:
    return f"reached the {max_turns}-turn limit before producing an answer"


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
