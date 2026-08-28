"""The inner loop: model, tools, model — and the budget enforced before spend."""

from __future__ import annotations

import pytest

from conftest import FakeModel, text_reply, tool_reply
from teacup_run.budget import Budget
from teacup_run.loop import run
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
    assert not result.stopped_early
