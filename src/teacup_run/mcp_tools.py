"""MCP — borrow other people's tools instead of writing them.

Every tool an agent uses otherwise has to be hand-written with `@tool` in the
package's own `tools.py`. MCP is the standard way to stop doing that: connect to a
server and its tools arrive ready for the model to call. Filesystem, GitHub,
browsers, databases, search — someone already wrote them, and any tool that speaks
the spec works here without teacup-run knowing anything about that specific server.
This is the sibling of `manifest.py`'s Agent Skills and AGENTS.md work — wrapping an
existing open standard for "tools" rather than inventing an incompatible one, which
is what makes a "framework-agnostic" package format an actual, checkable property
instead of a slogan.

Mirrors teacup-agent's own `mcp_tools.py` closely (same connection/dispatch shape,
same four load-bearing details below), adapted to this repo's tool model: teacup-run
has no global tool registry — `AutoAgent.tools` is a plain per-instance list — so
`McpHub.connect()` returns the `Tool` objects it created instead of mutating shared
state, and `AutoAgent.add_mcp_server()` extends its own list with them.

Four details do the real work:

1. **Names are namespaced.** Two servers may each expose `search`; the spec says
   clients aggregating servers must disambiguate. Tools land as `server__tool`,
   sanitized because tool names here allow only what `tools.py`'s own schema
   builder does — safe to be conservative and match `[A-Za-z0-9_-]`.
2. **Errors keep this repo's own discipline.** MCP separates protocol errors from
   *tool execution errors* (`isError: true`); the spec says clients SHOULD hand the
   latter to the model so it can self-correct, which is exactly what `tools.dispatch()`
   already does for a raised exception. Both end up as plain result text prefixed
   `ERROR:` rather than raising further.
3. **There is no approval gate here to plug into.** Unlike teacup-agent, this repo's
   native loop has no gated/ungated distinction on any tool at all yet — a locally
   authored `@tool` function already runs unattended with no human in the loop.
   Connecting an MCP server does not make that safer or less safe; it inherits the
   same trust model every other tool already has. `docs/backends.md`'s framing
   applies here too: this is a real, stated limitation, not an oversight, and a
   server's tools should only be added when the server itself is trusted, same as
   a `tools.py` you didn't write yourself.
4. **Async lives in one place.** The SDK is async; the rest of this repo's tool
   calling (`tools.dispatch`) is synchronous. One background event loop owns every
   MCP session, and the tool functions this module registers block on it. Nothing
   else in the codebase learns about asyncio.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
from contextlib import AsyncExitStack
from typing import Any

from .tools import Tool

__all__ = ["McpHub"]

CALL_TIMEOUT = 60.0  # seconds a single MCP tool call may take
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_-]")


def _tool_name(server: str, tool: str) -> str:
    return _SAFE_NAME.sub("_", f"{server}__{tool}")


def _result_text(result: Any) -> str:
    """Flatten a CallToolResult into the string a tool call returns."""
    if getattr(result, "result_type", "complete") == "input_required":
        # Multi round-trip requests: the server wants interactive input mid-call. Not
        # implemented — say so as an error the model can route around rather than
        # hanging or crashing.
        return (
            "ERROR: this tool asked for interactive input, which this agent does not "
            "support. Try a different tool, or supply the missing information as an "
            "argument."
        )

    parts = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
        elif getattr(block, "uri", None):  # resource_link
            parts.append(f"[resource] {block.uri}")
        else:
            parts.append(f"[{getattr(block, 'type', 'content')} omitted]")

    structured = getattr(result, "structured_content", None)
    if not parts and structured is not None:
        parts.append(json.dumps(structured, ensure_ascii=False))

    body = "\n".join(parts) or "(the tool returned no content)"
    return f"ERROR: {body}" if getattr(result, "is_error", False) else body


class McpHub:
    """Owns the event loop, the connections, and the tools they contributed.

    One hub per `AutoAgent`; `AutoAgent.add_mcp_server()` creates it lazily on first
    use and `AutoAgent.close()` tears it down. A hub with no servers connected costs
    nothing — the background thread only starts once `connect()` is actually called.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stack: AsyncExitStack | None = None
        self._clients: dict[str, Any] = {}
        self._devnull: Any = None

    def _ensure_started(self) -> None:
        if self._loop is not None:
            return
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._stack = AsyncExitStack()

    def _run(self, coro, timeout: float | None = None):
        """Block the calling (sync) thread on a coroutine in the background loop."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    # -- connecting ---------------------------------------------------------

    def connect(self, name: str, spec: dict[str, Any]) -> list[Tool]:
        """Connect to one server and return the `Tool`s it contributes.

        spec: `{"url": ...}` or `{"command": ..., "args": [...], "env": {...}}`,
        plus an optional `"tools"` allowlist and `"stderr"` (`"hide"` | `"show"`).
        Unlike teacup-agent's own hub there is no `"approve"` key — see the module
        docstring's third point.
        """
        self._ensure_started()
        from mcp import Client, StdioServerParameters
        from mcp.client.stdio import stdio_client

        if url := spec.get("url"):
            target: Any = url
        else:
            params = StdioServerParameters(
                command=spec["command"],
                args=spec.get("args", []),
                env=spec.get("env") or None,
            )
            # A stdio server logs to its own stderr, which lands in the caller's
            # terminal. Servers still on the pre-2026-07-28 protocol print a wall of
            # validation errors when this client probes with server/discover, so the
            # default is to hide it — "stderr": "show" is for when a server will not
            # start and you need to see why.
            if spec.get("stderr", "hide") == "show":
                target = params
            else:
                self._devnull = open(os.devnull, "w")
                target = stdio_client(params, errlog=self._devnull)

        try:
            client = self._run(self._stack.enter_async_context(Client(target)), timeout=60)
        except Exception as e:
            raise RuntimeError(
                f"could not connect to MCP server {name!r}: {type(e).__name__}: {e}. "
                'Set "stderr": "show" in the config to see the server\'s own output.'
            ) from e
        self._clients[name] = client

        listing = self._run(client.list_tools(), timeout=30)
        allow = set(spec.get("tools") or [])

        added = []
        for tool in listing.tools:
            if allow and tool.name not in allow:
                continue  # every tool schema costs prefix tokens on every request
            added.append(self._make_tool(name, tool))
        return added

    def _make_tool(self, server: str, tool: Any) -> Tool:
        name = _tool_name(server, tool.name)
        remote = tool.name

        def call(**kwargs: Any) -> str:
            client = self._clients[server]
            result = self._run(client.call_tool(remote, kwargs), timeout=CALL_TIMEOUT)
            return _result_text(result)

        call.__name__ = name
        schema = tool.input_schema or {"type": "object"}
        description = (tool.description or tool.title or remote).strip()
        return Tool(name=name, description=f"[{server}] {description}", parameters=schema, fn=call)

    # -- teardown -----------------------------------------------------------

    def close(self) -> None:
        if self._loop is None:
            return  # never actually connected to anything
        try:
            self._run(self._stack.aclose(), timeout=15)
        except Exception:
            pass  # a server that died during shutdown must not fail the run
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        if self._devnull is not None:
            self._devnull.close()
        self._loop = None


def load_config(path: str) -> dict[str, dict[str, Any]]:
    """Read a config file shaped like the one every MCP host uses:

    `{"servers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}}`
    """
    data = json.loads(open(path, encoding="utf-8").read())
    servers = data.get("servers", data)
    # Config files get comments; JSON has none. Keys starting with _ are dropped so
    # "_comment" can be used the way everyone uses it anyway.
    return {
        name: {k: v for k, v in spec.items() if not k.startswith("_")}
        for name, spec in servers.items()
    }
