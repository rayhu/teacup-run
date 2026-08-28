"""Provider dispatch and the message translation each branch needs.

No network: these test the pure functions around `call_model`, which is where
provider quirks live.
"""

from __future__ import annotations

import pytest

from teacup_run.model import (
    ProviderError,
    _gemini_schema,
    _strip_internal,
    _to_anthropic,
    _to_google,
    provider_for,
)


@pytest.mark.parametrize(
    ("model", "provider"),
    [
        ("gpt-5", "openai"),
        ("gpt-5-mini-2026-01-01", "openai"),
        ("claude-opus-5", "anthropic"),
        ("gemini-flash-latest", "google"),
    ],
)
def test_the_provider_comes_from_the_model_name(model, provider):
    assert provider_for(model) == provider


def test_an_unknown_model_names_the_prefixes_it_knows():
    with pytest.raises(ProviderError, match="gpt-"):
        provider_for("llama-4")


def test_internal_keys_never_leave_the_library():
    messages = [{"role": "assistant", "content": "hi", "_raw": {"parts": []}}]

    assert _strip_internal(messages) == [{"role": "assistant", "content": "hi"}]


def test_gemini_schema_drops_what_gemini_rejects():
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"city": {"type": "string", "title": "City"}},
    }

    cleaned = _gemini_schema(schema)

    assert "additionalProperties" not in cleaned
    assert "title" not in cleaned["properties"]["city"]
    assert cleaned["properties"]["city"]["type"] == "string"


def test_gemini_function_calls_are_replayed_verbatim():
    """Gemini rejects a history whose function calls lost their thought_signature."""
    native = [{"function_call": {"name": "save", "args": {"x": 1}}, "thought_signature": "sig-1"}]
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "_raw": {"parts": native},
            "tool_calls": [
                {"id": "1", "type": "function", "function": {"name": "save", "arguments": '{"x": 1}'}}
            ],
        },
        {"role": "tool", "tool_call_id": "1", "name": "save", "content": "ok"},
    ]

    contents = _to_google(messages)

    assert contents[1] == {"role": "model", "parts": native}
    assert contents[2]["parts"][0]["function_response"]["name"] == "save"


def test_gemini_falls_back_to_reconstructing_a_call_without_native_parts():
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "1", "type": "function", "function": {"name": "save", "arguments": '{"x": 1}'}}
            ],
        }
    ]

    contents = _to_google(messages)

    assert contents[0]["parts"][0]["function_call"] == {"name": "save", "args": {"x": 1}}


def test_anthropic_translation_moves_tool_results_into_user_blocks():
    messages = [
        {"role": "system", "content": "be useful"},
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "t1", "type": "function", "function": {"name": "save", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "t1", "name": "save", "content": "ok"},
    ]

    out = _to_anthropic(messages)

    assert all(m["role"] != "system" for m in out)      # system is passed separately
    assert out[1]["content"][0]["type"] == "tool_use"
    assert out[2]["content"][0]["type"] == "tool_result"
    assert out[2]["content"][0]["tool_use_id"] == "t1"


def test_anthropic_merges_parallel_tool_results_into_one_message():
    """Claude calls tools in parallel; every result must land in one user turn."""
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "t1", "type": "function", "function": {"name": "a", "arguments": "{}"}},
                {"id": "t2", "type": "function", "function": {"name": "b", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "t1", "name": "a", "content": "ok a"},
        {"role": "tool", "tool_call_id": "t2", "name": "b", "content": "ok b"},
        {"role": "user", "content": "carry on"},
    ]

    out = _to_anthropic(messages)

    assert len(out[0]["content"]) == 2                       # both tool_use blocks
    assert [b["tool_use_id"] for b in out[1]["content"]] == ["t1", "t2"]
    assert len(out) == 3                                     # assistant, results, user
    assert out[2]["content"] == "carry on"
