"""MCP integration, tested against a real MCP server over stdio.

The server is tests/fixtures/demo_mcp_server.py — real protocol, no network and no
npx, so this suite stays as hermetic as the rest.
"""

import sys

import pytest

from teacup_run.mcp_tools import McpHub, _result_text, _tool_name, load_config
from teacup_run.tools import dispatch

SERVER = {"command": sys.executable, "args": ["tests/fixtures/demo_mcp_server.py"]}


@pytest.fixture(scope="module")
def hub():
    h = McpHub()
    tools = h.connect("demo", SERVER)
    yield h, {t.name: t for t in tools}
    h.close()


# --- naming -------------------------------------------------------------------


def test_tool_names_are_namespaced_and_sanitized():
    """Two servers can each expose `search`, and this repo's own tool names allow
    only [A-Za-z0-9_-] — MCP names may contain dots."""
    assert _tool_name("fetch", "fetch") == "fetch__fetch"
    assert _tool_name("gh", "admin.tools.list") == "gh__admin_tools_list"


# --- connecting -----------------------------------------------------------------


def test_server_tools_come_back_as_real_tool_objects(hub):
    _, tools = hub
    assert {"demo__echo", "demo__other", "demo__explode"} <= set(tools)
    spec = tools["demo__echo"]
    assert spec.description.startswith("[demo]")  # the source server is visible
    assert spec.parameters["type"] == "object"  # the server's own JSON Schema


def test_allowlist_keeps_the_context_prefix_small():
    """Every tool schema costs prefix tokens on every request, so a server with
    several tools should not force all of them into the context."""
    h = McpHub()
    try:
        added = h.connect("small", {**SERVER, "tools": ["echo"]})
        assert [t.name for t in added] == ["small__echo"]
    finally:
        h.close()


def test_connecting_twice_reuses_the_same_background_loop():
    """add_mcp_server() may be called more than once; the hub should not spin up a
    second event loop/thread per server."""
    h = McpHub()
    try:
        h.connect("first", SERVER)
        loop_after_first = h._loop
        h.connect("second", {**SERVER, "tools": ["echo"]})
        assert h._loop is loop_after_first
    finally:
        h.close()


# --- calling ------------------------------------------------------------------


def test_a_call_goes_through_the_normal_dispatch_path(hub):
    _, tools = hub
    assert dispatch(tools, "demo__echo", {"text": "hello"}) == "echo: hello"


def test_tool_execution_errors_become_our_ERROR_string(hub):
    """MCP separates protocol errors from tool execution errors and says clients
    SHOULD hand the latter to the model. That is what dispatch() already does for a
    raised exception."""
    _, tools = hub
    assert dispatch(tools, "demo__explode", {}).startswith("ERROR:")


def test_bad_arguments_come_back_as_a_correctable_error(hub):
    _, tools = hub
    out = dispatch(tools, "demo__echo", {"wrong": 1})
    assert "Error" in out


# --- teardown -------------------------------------------------------------------


def test_close_stops_the_background_thread():
    h = McpHub()
    tools = h.connect("temp", SERVER)
    thread = h._thread
    assert thread.is_alive()
    h.close()
    thread.join(timeout=2)
    assert not thread.is_alive()
    # The Tool objects the caller already holds are not retroactively revoked —
    # there is no central registry here to remove them from, unlike teacup-agent's
    # hub. Calling one after close() would fail because the connection is gone, not
    # because the tool disappeared from anywhere.
    assert tools and tools[0].name == "temp__echo"


def test_close_is_a_no_op_when_nothing_ever_connected():
    McpHub().close()  # must not raise


# --- result conversion (unit, no server needed) --------------------------------


class _Block:
    def __init__(self, text=None, uri=None, type="text"):
        self.text, self.uri, self.type = text, uri, type


class _Result:
    def __init__(self, content=None, structured_content=None, is_error=False,
                 result_type="complete"):
        self.content = content
        self.structured_content = structured_content
        self.is_error = is_error
        self.result_type = result_type


def test_text_blocks_are_joined():
    assert _result_text(_Result([_Block("a"), _Block("b")])) == "a\nb"


def test_structured_content_is_used_when_there_is_no_text():
    assert _result_text(_Result(structured_content={"k": 1})) == '{"k": 1}'


def test_is_error_becomes_an_ERROR_prefix():
    assert _result_text(_Result([_Block("nope")], is_error=True)) == "ERROR: nope"


def test_input_required_is_reported_rather_than_hung_on():
    """Multi round-trip requests want interactive input mid-call. Not implemented,
    so it becomes an error the model can route around."""
    out = _result_text(_Result(result_type="input_required"))
    assert out.startswith("ERROR:") and "interactive input" in out


def test_empty_results_say_so_instead_of_returning_nothing():
    assert "no content" in _result_text(_Result([]))


# --- config -------------------------------------------------------------------


def test_config_accepts_the_usual_shape(tmp_path):
    import json

    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"servers": {"fetch": {"command": "uvx"}}}), encoding="utf-8")
    assert load_config(str(path)) == {"fetch": {"command": "uvx"}}


def test_config_also_accepts_a_bare_mapping(tmp_path):
    import json

    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"fetch": {"command": "uvx"}}), encoding="utf-8")
    assert load_config(str(path)) == {"fetch": {"command": "uvx"}}


def test_config_drops_comment_keys(tmp_path):
    """JSON has no comments, and every config file grows them anyway."""
    import json

    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps({"servers": {"fetch": {"command": "uvx", "_comment": "why"}}}),
        encoding="utf-8",
    )
    assert load_config(str(path)) == {"fetch": {"command": "uvx"}}
