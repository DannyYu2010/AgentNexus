from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent_memory_hub.project_store import ProjectStore
from agent_memory_hub.schemas import ProjectCreate

ROOT = Path(__file__).parents[1]
LAUNCHER = ROOT / "scripts/agent_memory_launcher.py"


def test_state_only_launcher_serves_project_state_without_mem0(tmp_path: Path) -> None:
    support = tmp_path / "support"
    support.mkdir()
    (support / "mode.json").write_text('{"mode":"state-only"}', encoding="utf-8")
    data_dir = tmp_path / "data"
    store = ProjectStore(data_dir / "project_state.db")
    store.initialize()
    store.create_project(ProjectCreate(id="lean", name="Lean"))
    parameters = StdioServerParameters(
        command=str(LAUNCHER),
        env={
            "AGENTNEXUS_SUPPORT_DIR": str(support),
            "AGENTNEXUS_PROJECT_ROOT": str(ROOT),
            "MEMORY_DATA_DIR": str(data_dir),
            "MEM0_TELEMETRY": "false",
        },
    )

    async def invoke() -> None:
        async with (
            stdio_client(parameters) as (read_stream, write_stream),
            ClientSession(read_stream, write_stream) as session,
        ):
            await session.initialize()
            bootstrap = await session.call_tool("bootstrap", {"project_id": "lean"})
            start = await session.call_tool(
                "start_turn", {"project_id": "lean", "content": "state-only event"}
            )
            assert bootstrap.structured_content is not None
            assert start.structured_content is not None
            bootstrap_payload = json.loads(json.dumps(bootstrap.structured_content))
            start_payload = json.loads(json.dumps(start.structured_content))
            assert bootstrap_payload["ok"] is True
            assert bootstrap_payload["result"]["project"]["id"] == "lean"
            assert start_payload["result"]["error"]["code"] == "MEM0_DISABLED"

    asyncio.run(invoke())
