"""P3 smoke test using a real MCP client and stdio server subprocess."""

from __future__ import annotations

import sys
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS = {
    "bootstrap",
    "correct_memory",
    "finish_turn",
    "get_state",
    "search",
    "start_turn",
    "update_state",
}


async def verify() -> None:
    project_root = Path(__file__).resolve().parents[1]
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agent_memory_hub.mcp_server"],
        cwd=project_root,
    )
    async with (
        stdio_client(parameters) as (read_stream, write_stream),
        ClientSession(read_stream, write_stream) as session,
    ):
        initialized = await session.initialize()
        tools = await session.list_tools()
        names = {tool.name for tool in tools.tools}
        if names != EXPECTED_TOOLS:
            raise AssertionError(
                f"MCP tool mismatch: expected {sorted(EXPECTED_TOOLS)}, got {sorted(names)}"
            )

        result = await session.call_tool(
            "bootstrap", {"project_id": "p3-stdio-smoke-missing"}
        )
        payload = result.structured_content
        if not isinstance(payload, dict):
            raise TypeError(f"MCP response is not structured JSON: {payload!r}")
        error = payload.get("error") or {}
        if payload.get("ok") is not False or error.get("code") != "PROJECT_NOT_FOUND":
            raise AssertionError(f"Unexpected bootstrap error contract: {payload!r}")

        print(f"MCP server: {initialized.server_info.name}")
        print(f"Tools: {', '.join(sorted(names))}")
        print("Structured PROJECT_NOT_FOUND response verified")


def main() -> int:
    try:
        anyio.run(verify)
    except Exception as exc:  # noqa: BLE001 - CLI boundary reports failures cleanly.
        print(f"P3 MCP stdio smoke test failed: {exc}", file=sys.stderr)
        return 1
    print("P3 MCP stdio smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
