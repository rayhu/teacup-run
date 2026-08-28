"""Plain Python functions become tools.

A tool is a function plus the JSON schema a model needs to call it. The schema is
derived from type hints and the docstring, so there is nothing to keep in sync by
hand — and nothing to learn beyond writing an ordinary function.

    @tool
    def get_weather(city: str, days: int = 1) -> dict:
        '''Look up a forecast.

        Args:
            city: The city to look up.
            days: How many days ahead, 1-7.
        '''

A tool that needs to remember something across calls takes `artifacts` as its
first parameter. The run passes its shared dict; the model never sees it, and
goal checks read what the tools wrote there.

    @tool
    def save_note(artifacts: dict, claim: str) -> dict:
        '''Record a finding.'''
        artifacts.setdefault("notes", []).append(claim)
        return {"saved": True}
"""

from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass
from typing import Any, Callable, get_args, get_origin, get_type_hints

__all__ = ["Tool", "ToolError", "dispatch", "tool"]


class ToolError(ValueError):
    """A tool could not be described to the model, or could not be called."""


# v0 supports what a tool signature actually needs. Anything else raises at
# definition time rather than producing a schema the model will misuse.
JSON_TYPES: dict[Any, dict[str, Any]] = {
    str: {"type": "string"},
    int: {"type": "integer"},
    float: {"type": "number"},
    bool: {"type": "boolean"},
    list[str]: {"type": "array", "items": {"type": "string"}},
    list[int]: {"type": "array", "items": {"type": "integer"}},
    list[float]: {"type": "array", "items": {"type": "number"}},
}


ARTIFACTS_PARAM = "artifacts"


@dataclass
class Tool:
    """A callable the model may invoke, plus its schema."""

    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., Any]
    wants_artifacts: bool = False

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.fn(*args, **kwargs)


def tool(fn: Callable[..., Any]) -> Tool:
    """Turn a function into a Tool, deriving its schema from hints and docstring."""
    doc = inspect.getdoc(fn) or ""
    summary = doc.split("\n\n")[0].strip() or fn.__name__
    arg_docs = _parse_args_section(doc)

    # Resolve annotations properly: a module using `from __future__ import
    # annotations` hands us the string "str", not the type.
    try:
        hints = get_type_hints(fn)
    except Exception as exc:  # noqa: BLE001 - a bad hint is the author's bug, reported as one
        raise ToolError(f"{fn.__name__} has type hints that cannot be resolved: {exc}") from exc

    properties: dict[str, Any] = {}
    required: list[str] = []
    parameters = list(inspect.signature(fn).parameters.items())

    # A leading `artifacts` parameter is injected by the run, not by the model,
    # so it is deliberately absent from the schema.
    wants_artifacts = bool(parameters) and parameters[0][0] == ARTIFACTS_PARAM
    if wants_artifacts:
        parameters = parameters[1:]

    for name, param in parameters:
        if param.annotation is inspect.Parameter.empty:
            raise ToolError(f"{fn.__name__}({name}) needs a type hint to become a tool")
        annotation = hints.get(name, param.annotation)
        schema = JSON_TYPES.get(annotation)
        if schema is None:
            raise ToolError(
                f"{fn.__name__}({name}: {_type_name(annotation)}) is not a supported "
                f"tool type. Supported: {', '.join(sorted(_type_name(t) for t in JSON_TYPES))}."
            )
        properties[name] = dict(schema)
        if name in arg_docs:
            properties[name]["description"] = arg_docs[name]
        if param.default is inspect.Parameter.empty:
            required.append(name)

    return Tool(
        name=fn.__name__,
        description=summary,
        parameters={
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        fn=fn,
        wants_artifacts=wants_artifacts,
    )


def dispatch(
    tools: dict[str, Tool],
    name: str,
    arguments: dict[str, Any],
    artifacts: dict[str, Any] | None = None,
) -> str:
    """Call a tool by name and return its result as text for the model.

    A tool that raises is reported back to the model rather than killing the run:
    an exception leaves it with nothing to say, an error message it can act on.
    """
    target = tools.get(name)
    if target is None:
        return f"Error: no tool named {name!r}. Available: {', '.join(sorted(tools)) or 'none'}."
    try:
        if target.wants_artifacts:
            result = target(artifacts if artifacts is not None else {}, **arguments)
        else:
            result = target(**arguments)
    except Exception as exc:  # noqa: BLE001 - deliberately surfaced to the model
        return f"Error calling {name}: {type(exc).__name__}: {exc}"
    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(result)


def _type_name(annotation: Any) -> str:
    origin = get_origin(annotation)
    if origin is list:
        inner = get_args(annotation)
        return f"list[{_type_name(inner[0])}]" if inner else "list"
    return getattr(annotation, "__name__", str(annotation))


_ARG_LINE = re.compile(r"^\s*(\w+)\s*(?:\([^)]*\))?:\s*(.+)$")


def _parse_args_section(doc: str) -> dict[str, str]:
    """Read a Google-style `Args:` block. Absent or malformed is fine — no descriptions."""
    lines = doc.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() in {"Args:", "Arguments:"})
    except StopIteration:
        return {}

    out: dict[str, str] = {}
    for line in lines[start + 1 :]:
        if line.strip() in {"Returns:", "Raises:", "Yields:", "Examples:"}:
            break
        match = _ARG_LINE.match(line)
        if match:
            out[match.group(1)] = match.group(2).strip()
    return out
