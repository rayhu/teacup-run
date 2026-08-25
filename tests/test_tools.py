"""A tool is a plain function; the schema comes from its hints and docstring."""

from __future__ import annotations

import pytest

from opengraft.tools import Tool, ToolError, dispatch, tool


@tool
def lookup(city: str, days: int = 1) -> dict:
    """Look up a forecast.

    Args:
        city: The city to look up.
        days: How many days ahead.
    """
    return {"city": city, "days": days}


def test_schema_comes_from_hints_and_docstring():
    schema = lookup.schema()

    assert schema["name"] == "lookup"
    assert schema["description"] == "Look up a forecast."
    assert schema["parameters"]["properties"]["city"] == {
        "type": "string",
        "description": "The city to look up.",
    }
    assert schema["parameters"]["properties"]["days"]["type"] == "integer"


def test_only_arguments_without_defaults_are_required():
    assert lookup.schema()["parameters"]["required"] == ["city"]


def test_an_unsupported_type_fails_at_definition_time():
    with pytest.raises(ToolError, match="not a supported tool type"):

        @tool
        def bad(payload: dict) -> str:
            """Takes something the model cannot be told how to build."""
            return ""


def test_a_missing_hint_fails_at_definition_time():
    with pytest.raises(ToolError, match="needs a type hint"):

        @tool
        def bad(city) -> str:  # noqa: ANN001 - deliberately unannotated
            """No hint."""
            return ""


def test_artifacts_is_injected_not_exposed():
    @tool
    def remember(artifacts: dict, fact: str) -> dict:
        """Record something."""
        artifacts.setdefault("facts", []).append(fact)
        return {"count": len(artifacts["facts"])}

    assert remember.wants_artifacts
    assert "artifacts" not in remember.schema()["parameters"]["properties"]

    state: dict = {}
    dispatch({"remember": remember}, "remember", {"fact": "surfskis are narrow"}, state)

    assert state["facts"] == ["surfskis are narrow"]


def test_a_raising_tool_reports_back_instead_of_killing_the_run():
    @tool
    def explode(why: str) -> str:
        """Always fails."""
        raise RuntimeError(why)

    out = dispatch({"explode": explode}, "explode", {"why": "no network"})

    assert "Error calling explode" in out
    assert "no network" in out


def test_an_unknown_tool_name_is_reported_to_the_model():
    out = dispatch({"lookup": lookup}, "teleport", {})

    assert "no tool named 'teleport'" in out
    assert "lookup" in out
