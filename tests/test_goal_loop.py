"""The outer loop: check the goal, feed the gap back, and keep the best attempt.

The keep-best rule is not a nicety. Measured on an earlier iteration of this
project, a loop that returned its *last* attempt lost to no loop at all: fed its
own failures, the model talked itself into a worse answer. Retrying must only be
able to cost money, never quality.
"""

from __future__ import annotations

from conftest import FakeModel, text_reply
from opengraft.goal import Attempt, GoalVerdict, evaluate
from opengraft.loop import run

ALWAYS_OK = {"ok": lambda a: ""}
ALWAYS_BAD = {"bad": lambda a: "Cite a source you actually read."}


def test_a_met_goal_stops_after_one_attempt():
    model = FakeModel(text_reply("good"), text_reply("unused"))

    result = run(
        "task", model="gpt-5", instructions="", checks=ALWAYS_OK,
        goal_checks=["ok"], max_attempts=3, model_fn=model,
    )

    assert result.attempts == 1
    assert result.goal.met
    assert len(model.calls) == 1


def test_an_unmet_goal_retries_with_the_gap_as_the_prompt():
    model = FakeModel(text_reply("first try"), text_reply("second try"))
    checks = {"bad": lambda a: "" if "second" in a.answer else "Say 'second'."}

    result = run(
        "task", model="gpt-5", instructions="", checks=checks,
        goal_checks=["bad"], goal_description="Say second.", max_attempts=3, model_fn=model,
    )

    assert result.attempts == 2
    assert result.answer == "second try"
    assert result.goal.met

    second_prompt = model.calls[1][-1]["content"]
    assert "Say 'second'." in second_prompt      # the gap
    assert "first try" in second_prompt          # the previous answer, to revise
    assert "attempt 2 of 3" in second_prompt


def test_it_gives_up_at_max_attempts():
    model = FakeModel(*[text_reply(f"try {i}") for i in range(5)])

    result = run(
        "task", model="gpt-5", instructions="", checks=ALWAYS_BAD,
        goal_checks=["bad"], max_attempts=3, model_fn=model,
    )

    assert result.attempts == 3
    assert not result.goal.met
    assert not result.stopped_early  # gave up on quality, not on resources


def test_it_keeps_the_best_attempt_not_the_last():
    """Attempt 1 passes two checks; attempts 2 and 3 pass none. Keep attempt 1."""
    model = FakeModel(
        text_reply("good answer with detail"),
        text_reply("worse"),
        text_reply("worst"),
    )
    checks = {
        "a": lambda att: "" if "good" in att.answer else "be good",
        "b": lambda att: "" if "detail" in att.answer else "add detail",
        "c": lambda att: "never satisfied",
    }

    result = run(
        "task", model="gpt-5", instructions="", checks=checks,
        goal_checks=["a", "b", "c"], max_attempts=3, model_fn=model,
    )

    assert result.attempts == 3
    assert result.answer == "good answer with detail"
    assert result.goal.passed == 2


def test_no_goal_checks_means_no_second_attempt():
    model = FakeModel(text_reply("one"), text_reply("two"))

    result = run("task", model="gpt-5", instructions="", max_attempts=3, model_fn=model)

    assert result.attempts == 1
    assert result.goal is None


def test_artifacts_persist_across_attempts():
    """A retry builds on what was gathered rather than starting from nothing."""
    seen: list[int] = []

    def counting_check(attempt: Attempt) -> str:
        attempt.artifacts["attempts_seen"] = attempt.artifacts.get("attempts_seen", 0) + 1
        seen.append(attempt.artifacts["attempts_seen"])
        return "again"

    model = FakeModel(*[text_reply(f"try {i}") for i in range(3)])
    result = run(
        "task", model="gpt-5", instructions="", checks={"c": counting_check},
        goal_checks=["c"], max_attempts=3, model_fn=model,
    )

    assert seen == [1, 2, 3]
    assert result.artifacts["attempts_seen"] == 3


def test_the_verdict_reports_which_checks_failed():
    verdict = evaluate(
        {"a": lambda x: "", "b": lambda x: "fix b"}, ["a", "b"], Attempt(answer="x")
    )

    assert isinstance(verdict, GoalVerdict)
    assert not verdict.met
    assert verdict.failed == ("b",)
    assert verdict.passed == 1
    assert "failed: b" in verdict.summary()
