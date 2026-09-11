"""The inner loop: model, tools, model — and the budget enforced before spend."""

from __future__ import annotations

import pytest

from conftest import FakeModel, text_reply, tool_reply
from teacup_run.budget import Budget
from teacup_run.loop import run
from teacup_run.model import Reply
from teacup_run.tools import tool


@tool
def note(artifacts: dict, text: str) -> dict:
    """Record something.

    Args:
        text: What to record.
    """
    artifacts.setdefault("notes", []).append(text)
    return {"count": len(artifacts["notes"])}


def test_a_reply_without_tool_calls_ends_the_turn():
    model = FakeModel(text_reply("done"))

    result = run("task", model="gpt-5", instructions="be useful", model_fn=model)

    assert result.answer == "done"
    assert result.attempts == 1
    assert len(result.ledger.model_calls) == 1


def test_tool_calls_are_executed_and_fed_back():
    model = FakeModel(
        tool_reply("note", {"text": "first"}),
        text_reply("recorded it"),
    )

    result = run("task", model="gpt-5", instructions="", tools=[note], model_fn=model)

    assert result.answer == "recorded it"
    assert result.artifacts["notes"] == ["first"]
    assert result.tool_calls == ("note",)
    # The tool's output is handed back to the model as a tool message.
    second_call = model.calls[1]
    assert second_call[-1]["role"] == "tool"
    assert "count" in second_call[-1]["content"]


def test_the_budget_stops_the_run_before_the_next_call():
    # One model call costs more than this ceiling, so the second must not happen.
    model = FakeModel(
        text_reply("first", input_tokens=1_000_000, output_tokens=1_000_000),
        text_reply("second"),
    )

    result = run(
        "task",
        model="gpt-5",
        instructions="",
        budget=Budget(usd=0.01),
        checks={"always_fails": lambda a: "keep going"},
        goal_checks=["always_fails"],
        max_attempts=3,
        model_fn=model,
    )

    assert result.stopped_early
    assert "budget" in result.stop_reason
    assert len(model.calls) == 1


def test_a_partial_answer_survives_a_budget_stop():
    model = FakeModel(
        text_reply("a usable first answer", input_tokens=1_000_000, output_tokens=1_000_000),
        text_reply("never reached"),
    )

    result = run(
        "task",
        model="gpt-5",
        instructions="",
        budget=Budget(usd=2.00),
        checks={"always_fails": lambda a: "not good enough"},
        goal_checks=["always_fails"],
        max_attempts=3,
        model_fn=model,
    )

    assert result.stopped_early
    assert result.answer == "a usable first answer"


def test_tool_calls_count_against_the_ceiling():
    model = FakeModel(
        tool_reply("note", {"text": "one"}),
        tool_reply("note", {"text": "two"}),
        text_reply("done"),
    )

    result = run(
        "task",
        model="gpt-5",
        instructions="",
        tools=[note],
        budget=Budget(max_tool_calls=1),
        model_fn=model,
    )

    assert result.stopped_early
    assert "tool-call limit" in result.stop_reason
    assert len(result.ledger.tool_calls) == 1


def test_the_turn_limit_ends_a_tool_loop_that_never_answers():
    model = FakeModel(*[tool_reply("note", {"text": str(i)}) for i in range(20)])

    result = run(
        "task", model="gpt-5", instructions="", tools=[note], max_turns=3, model_fn=model
    )

    assert len(result.ledger.model_calls) == 3
    # It *is* an early stop, and used not to be. Reaching the limit means the model was
    # still calling tools on the last allowed turn, so the run never produced an answer
    # — and reporting that as a completed run made `teacup run` exit 0, telling CI a
    # run that ran out of turns had succeeded. A ceiling you set is exit 2, the same as
    # the tool-call limit above.
    assert result.stopped_early
    assert result.stop_kind == "budget"
    assert "3-turn limit" in result.stop_reason


def test_the_turn_limit_still_evaluates_the_goal():
    """The half the first version of this fix silently dropped.

    Raising at the turn limit skipped `evaluate()`, so `Result.goal` came back None,
    `evaluate.py` scored `goal_met=None` as 0, and a benchmark task that exhausted its
    turns but *passed its checks* lost 0.3 of its quality — enough to cross the 0.5
    success threshold. Only the `goal-loop` arm declares checks, so `compare` became
    biased against the very thing it measures. Asserting on the text alone could not
    see it.
    """
    replies = [tool_reply("note", {"text": str(i)}) for i in range(20)]
    replies[2] = Reply(
        text="alpha beta gamma",
        tool_calls=replies[2].tool_calls,
        usage=replies[2].usage,
    )
    model = FakeModel(*replies)

    result = run(
        "task",
        model="gpt-5",
        instructions="",
        tools=[note],
        checks={"non_empty": lambda attempt: "" if attempt.answer.strip() else "no answer"},
        goal_checks=("non_empty",),
        max_turns=3,
        model_fn=model,
    )

    assert result.goal is not None, "the goal was never evaluated"
    assert result.goal.met is True
    assert "alpha beta gamma" in result.answer
    # Met its goal on the last allowed turn: that is a completed run, not an early
    # stop, and calling it one would exit 2 for a success.
    assert not result.stopped_early


def test_the_turn_limit_is_an_early_stop_when_the_goal_was_not_met():
    """The check has to be one the fallback sentence cannot satisfy.

    Written first against `non_empty`, which the fallback *does* satisfy — so the test
    was asserting the regression (an empty answer) rather than the behaviour.
    """
    replies = [tool_reply("note", {"text": str(i)}) for i in range(20)]
    model = FakeModel(*replies)

    result = run(
        "task",
        model="gpt-5",
        instructions="",
        tools=[note],
        checks={"says_done": lambda attempt: "" if "DONE" in attempt.answer else "never said DONE"},
        goal_checks=("says_done",),
        max_turns=3,
        model_fn=model,
    )

    assert result.goal is not None and result.goal.met is False
    assert result.stopped_early
    assert result.stop_kind == "budget"


def test_the_turn_limit_still_answers_rather_than_returning_nothing():
    """`reply.text` is "" for a tool-calling turn, which is the ordinary shape of the
    reply that hits the limit. Dropping the `or <sentence>` fallback cost three things
    at once: `evaluate.score` lost its keyword hits, `non_empty` began failing so the
    goal loop retried (1 attempt became 3 — a silent 3x in spend), and the human path
    lost the only place the turn limit was ever named."""
    model = FakeModel(*[tool_reply("note", {"text": str(i)}) for i in range(20)])

    result = run("task", model="gpt-5", instructions="", tools=[note], max_turns=3, model_fn=model)

    assert result.answer.strip(), "a turn-limited run must still say something"
    assert "turn limit" in result.answer


def test_a_ceiling_on_a_later_attempt_does_not_relabel_an_earlier_complete_answer():
    """`best` can hold attempt 1 while the loop exits on attempt 3. Reading the last
    attempt's flag reported "reached the turn limit before producing an answer" beside
    a complete answer from an attempt that hit no ceiling, and turned a plain
    goal-not-met run (exit 1) into a budget stop (exit 2)."""
    # Longer than the turn-limit fallback sentence, so `best`'s length tiebreak really
    # does keep attempt 1. (The two round-3 defects hid each other: with the fallback
    # dropped, attempt 2's answer was "" and attempt 1 won on length automatically —
    # which is how this was first seen. Restoring the sentence makes the ranking
    # honest, so the scenario has to earn its place at the top rather than inherit it.)
    first = text_reply(
        "A complete answer that simply misses the goal, at more length than the "
        "turn-limit fallback sentence so that it genuinely ranks highest."
    )
    model = FakeModel(first, *[tool_reply("note", {"text": str(i)}) for i in range(20)])

    result = run(
        "task",
        model="gpt-5",
        instructions="",
        tools=[note],
        checks={"says_done": lambda attempt: "" if "DONE" in attempt.answer else "never said DONE"},
        goal_checks=("says_done",),
        max_attempts=2,
        max_turns=3,
        model_fn=model,
    )

    assert result.answer.startswith("A complete answer that simply misses the goal")
    assert not result.stopped_early, "the returned answer hit no ceiling"
    assert result.stop_kind is None
    assert result.goal is not None and result.goal.met is False
