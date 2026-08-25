"""The one place OpenGraft talks to a model provider.

Deliberately a function rather than a class hierarchy: adding a provider is one
`elif`, and faking the model in tests is one `monkeypatch`. Everything above this
module — the loop, the budget, the goal checks — is provider-agnostic because
this function normalises the reply.

Message shape is OpenAI's chat format, used as the neutral wire format; the other
branches translate to and from it.

One convention: message keys starting with `_` are OpenGraft's own and are
stripped before anything is sent. They carry provider-native data that has to be
replayed verbatim — Gemini, for instance, rejects a conversation whose function
calls come back without the `thought_signature` it issued.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Sequence

__all__ = ["Reply", "ToolCall", "Usage", "call_model", "provider_for"]


class ProviderError(RuntimeError):
    """The model could not be called: unknown provider, or its SDK is missing."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0


@dataclass(frozen=True)
class Reply:
    """One model turn: some text, some tool calls, and what it cost in tokens."""

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage = field(default_factory=Usage)
    raw_message: dict[str, Any] | None = None


PROVIDERS = {"gpt-": "openai", "o1": "openai", "o3": "openai", "claude-": "anthropic", "gemini-": "google"}


def provider_for(model: str) -> str:
    for prefix, provider in PROVIDERS.items():
        if model.startswith(prefix):
            return provider
    raise ProviderError(
        f"no provider for model {model!r}. Known prefixes: {', '.join(sorted(PROVIDERS))}."
    )


def call_model(
    model: str,
    messages: list[dict[str, Any]],
    tools: Sequence[dict[str, Any]] = (),
) -> Reply:
    """Send one turn to the model and normalise what comes back.

    Args:
        model: Provider is inferred from the name, e.g. "gpt-5", "claude-opus-5".
        messages: OpenAI-shaped chat messages.
        tools: JSON-schema tool definitions (see tools.schema_for).
    """
    provider = provider_for(model)
    if provider == "openai":
        return _call_openai(model, messages, tools)
    if provider == "anthropic":
        return _call_anthropic(model, messages, tools)
    return _call_google(model, messages, tools)


def _import(name: str, extra: str):
    try:
        return __import__(name)
    except ImportError as exc:  # pragma: no cover - depends on what is installed
        raise ProviderError(
            f"{name} is not installed. Install it with: pip install 'opengraft[{extra}]'"
        ) from exc


def _strip_internal(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop OpenGraft-internal keys before a message goes to a provider."""
    return [{k: v for k, v in m.items() if not k.startswith("_")} for m in messages]


def _call_openai(model, messages, tools) -> Reply:
    openai = _import("openai", "openai")
    client = openai.OpenAI()
    response = client.chat.completions.create(
        model=model,
        messages=_strip_internal(messages),
        tools=[{"type": "function", "function": t} for t in tools] or None,
    )
    choice = response.choices[0].message
    calls = tuple(
        ToolCall(id=c.id, name=c.function.name, arguments=json.loads(c.function.arguments or "{}"))
        for c in (choice.tool_calls or [])
    )
    usage = response.usage
    return Reply(
        text=choice.content or "",
        tool_calls=calls,
        usage=Usage(
            input_tokens=getattr(usage, "prompt_tokens", 0),
            output_tokens=getattr(usage, "completion_tokens", 0),
            cached_input_tokens=getattr(
                getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0
            )
            or 0,
        ),
        raw_message=choice.model_dump(exclude_none=True),
    )


def _call_anthropic(model, messages, tools) -> Reply:
    anthropic = _import("anthropic", "anthropic")
    client = anthropic.Anthropic()
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system or anthropic.NOT_GIVEN,
        messages=_to_anthropic(_strip_internal(messages)),
        tools=[
            {"name": t["name"], "description": t.get("description", ""), "input_schema": t["parameters"]}
            for t in tools
        ]
        or anthropic.NOT_GIVEN,
    )
    text = "".join(b.text for b in response.content if b.type == "text")
    calls = tuple(
        ToolCall(id=b.id, name=b.name, arguments=dict(b.input))
        for b in response.content
        if b.type == "tool_use"
    )
    return Reply(
        text=text,
        tool_calls=calls,
        usage=Usage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cached_input_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        ),
    )


def _to_anthropic(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI-shaped history -> Anthropic content blocks.

    Consecutive tool results are merged into one user message: when an assistant
    turn makes several tool calls at once, Anthropic expects every result for
    that turn in the message that follows it, not one message each.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        if m["role"] == "system":
            continue
        if m["role"] == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": m["tool_call_id"],
                "content": m["content"],
            }
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        elif m["role"] == "assistant" and m.get("tool_calls"):
            blocks: list[dict[str, Any]] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for c in m["tool_calls"]:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": c["id"],
                        "name": c["function"]["name"],
                        "input": json.loads(c["function"]["arguments"] or "{}"),
                    }
                )
            out.append({"role": "assistant", "content": blocks})
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


def _call_google(model, messages, tools) -> Reply:
    genai = _import("google", "google")  # noqa: F841 - the real import is below
    from google import genai as google_genai
    from google.genai import types

    client = google_genai.Client()
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    config = types.GenerateContentConfig(
        system_instruction=system or None,
        tools=[
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name=t["name"],
                        description=t.get("description", ""),
                        parameters=_gemini_schema(t["parameters"]),
                    )
                    for t in tools
                ]
            )
        ]
        if tools
        else None,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    response = client.models.generate_content(
        model=model, contents=_to_google(messages), config=config
    )
    calls = tuple(
        ToolCall(id=f"{part.function_call.name}-{i}", name=part.function_call.name,
                 arguments=dict(part.function_call.args or {}))
        for i, part in enumerate(_google_parts(response))
        if part.function_call
    )
    usage = response.usage_metadata
    return Reply(
        text=_google_text(response),
        tool_calls=calls,
        raw_message={"parts": [_dump(part) for part in _google_parts(response)]},
        usage=Usage(
            input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
            cached_input_tokens=getattr(usage, "cached_content_token_count", 0) or 0,
        ),
    )


def _dump(obj: Any) -> dict[str, Any]:
    """A provider object as a plain dict, so it can be replayed verbatim."""
    for method in ("model_dump", "to_json_dict", "dict"):
        dumper = getattr(obj, method, None)
        if callable(dumper):
            try:
                return dumper(exclude_none=True) if method == "model_dump" else dumper()
            except TypeError:
                return dumper()
    return dict(obj)


def _google_text(response) -> str:
    """The text parts only — `response.text` warns when tool calls are present."""
    return "".join(
        part.text for part in _google_parts(response) if getattr(part, "text", None)
    )


def _gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Gemini accepts a subset of JSON Schema: drop what it rejects."""
    unsupported = {"additionalProperties", "$schema", "default", "title"}
    return {
        key: (_gemini_schema(value) if isinstance(value, dict) else value)
        for key, value in schema.items()
        if key not in unsupported
    }


def _google_parts(response) -> list[Any]:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return []
    return list(getattr(candidates[0].content, "parts", None) or [])


def _to_google(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenAI-shaped history -> Gemini contents."""
    out: list[dict[str, Any]] = []
    for m in messages:
        if m["role"] == "system":
            continue
        if m["role"] == "tool":
            out.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "function_response": {
                                "name": m.get("name", "tool"),
                                "response": {"result": m["content"]},
                            }
                        }
                    ],
                }
            )
        elif m["role"] == "assistant" and m.get("tool_calls"):
            # Replay Gemini's own parts when we have them: it requires the
            # thought_signature it issued to come back with the function call.
            raw_parts = (m.get("_raw") or {}).get("parts")
            if raw_parts:
                out.append({"role": "model", "parts": raw_parts})
                continue
            parts = [{"text": m["content"]}] if m.get("content") else []
            for c in m["tool_calls"]:
                parts.append(
                    {
                        "function_call": {
                            "name": c["function"]["name"],
                            "args": json.loads(c["function"]["arguments"] or "{}"),
                        }
                    }
                )
            out.append({"role": "model", "parts": parts})
        else:
            role = "model" if m["role"] == "assistant" else "user"
            out.append({"role": role, "parts": [{"text": m["content"]}]})
    return out
