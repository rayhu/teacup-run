"""A tiny MCP server used by the tests — real protocol, no network, no npx.

Mirrors teacup-agent's own tests/fixtures/demo_mcp_server.py (same repo, same
mechanism, no code shared between them). teacup-run's hub has no approval gate to
exercise, so this fixture skips the annotation variety that server's tests use for
that and keeps just enough tools to exercise naming, calling, and error handling.
"""

from mcp.server.mcpserver import MCPServer

server = MCPServer("demo")


@server.tool(description="Echo a string back.")
def echo(text: str) -> str:
    return f"echo: {text}"


@server.tool(description="A second tool, to exercise an allowlist.")
def other(value: str) -> str:
    return f"got {value}"


@server.tool(description="Always fails, as a tool execution error.")
def explode() -> str:
    raise ValueError("boom")


if __name__ == "__main__":
    server.run("stdio")
