"""The library's builtin checks, and the example agent's own."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from opengraft.goal import BUILTIN_CHECKS, Attempt

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "note-taker"
sys.path.insert(0, str(EXAMPLE))
import checks as note_taker_checks  # noqa: E402


def test_non_empty():
    assert BUILTIN_CHECKS["non_empty"](Attempt(answer="something")) == ""
    assert BUILTIN_CHECKS["non_empty"](Attempt(answer="   "))


@pytest.mark.parametrize(
    "answer",
    [
        "Here are the items. Which do you want?",
        "I could not reach the tools. Would you like me to retry instead?",
    ],
)
def test_no_question_back_catches_a_deferral(answer):
    assert BUILTIN_CHECKS["no_question_back"](Attempt(answer=answer))


def test_no_question_back_allows_an_ordinary_question_in_the_body():
    answer = "Open questions\n- Who owns the migration doc?\n\nAction items\n- Sam: announce"
    assert BUILTIN_CHECKS["no_question_back"](Attempt(answer=answer)) == ""


def test_used_a_tool():
    assert BUILTIN_CHECKS["used_a_tool"](Attempt(answer="x", tool_calls=("search",))) == ""
    assert BUILTIN_CHECKS["used_a_tool"](Attempt(answer="x"))


def test_has_action_items():
    empty = Attempt(answer="x", artifacts={})
    filled = Attempt(answer="x", artifacts={"action_items": [{"what": "a", "owner": "Ray"}]})

    assert "save_action_item" in note_taker_checks.has_action_items(empty)
    assert note_taker_checks.has_action_items(filled) == ""


def test_every_item_has_owner_names_the_offenders():
    attempt = Attempt(
        answer="x",
        artifacts={"action_items": [{"what": "write the doc", "owner": ""}]},
    )

    reason = note_taker_checks.every_item_has_owner(attempt)

    assert "write the doc" in reason
    assert "unassigned" in reason


def test_items_appear_in_answer_catches_a_recorded_but_unlisted_item():
    attempt = Attempt(
        answer="I reviewed the notes.",
        artifacts={"action_items": [{"what": "ship the pricing page", "owner": "Ray"}]},
    )

    assert "ship the pricing page" in note_taker_checks.items_appear_in_answer(attempt)

    listed = Attempt(answer="- Ray: ship the pricing page", artifacts=attempt.artifacts)
    assert note_taker_checks.items_appear_in_answer(listed) == ""


def test_the_example_agents_checks_are_registered():
    for name in ("has_action_items", "every_item_has_owner", "items_appear_in_answer"):
        assert getattr(getattr(note_taker_checks, name), "is_opengraft_check", False)
