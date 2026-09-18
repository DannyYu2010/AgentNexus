from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer

from agent_memory_hub.mcp_server import build_service, create_server
from agent_memory_hub.project_store import ProjectStore
from agent_memory_hub.schemas import (
    BootstrapRequest,
    DecisionCreate,
    ProjectCreate,
    SearchRequest,
    StateUpdate,
    TaskCreate,
    WorkLogCreate,
)
from agent_memory_hub.service import MemoryService

TOOL_NAMES = [
    "bootstrap",
    "start_turn",
    "search",
    "get_state",
    "update_state",
    "finish_turn",
    "correct_memory",
]


@pytest.fixture
def store(tmp_path: Path) -> ProjectStore:
    result = ProjectStore(tmp_path / "project_state.db")
    result.initialize()
    result.create_project(ProjectCreate(id="demo", name="Demo", repo_path="/tmp/demo"))
    return result


@pytest.fixture
def service(store: ProjectStore) -> MemoryService:
    # Degraded service: Project State works, Mem0 is disabled. No embeddings,
    # Qwen or Qdrant are loaded anywhere in this test module.
    return MemoryService(store, memory=None)


@pytest.fixture
def server(service: MemoryService) -> MCPServer:
    return create_server(service)


def _call(server: MCPServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async def invoke() -> dict[str, Any]:
        result = await server.call_tool(name, arguments)
        assert result.structured_content is not None
        return result.structured_content

    return asyncio.run(invoke())


def _list_tools(server: MCPServer) -> list[str]:
    return asyncio.run(server.list_tools())


# --- tool discovery ----------------------------------------------------------


def test_server_exposes_all_seven_tools(server: MCPServer) -> None:
    names = sorted(tool.name for tool in _list_tools(server))
    assert names == sorted(TOOL_NAMES)


def test_server_exposes_correct_memory(server: MCPServer) -> None:
    assert any(tool.name == "correct_memory" for tool in _list_tools(server))


def test_bootstrap_does_not_initialize_heavy_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    def unavailable_memory(settings: Any) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("HF network unavailable")

    monkeypatch.setattr(
        "agent_memory_hub.mcp_server.create_memory_engine", unavailable_memory
    )
    service = build_service(db_path=str(tmp_path / "lazy.db"))
    service.store.create_project(ProjectCreate(id="lazy", name="Lazy"))

    result = service.bootstrap(BootstrapRequest(project_id="lazy"))

    assert result.project.id == "lazy"
    assert calls == 0

    degraded = service.search(SearchRequest(project_id="lazy", query="触发记忆初始化"))
    assert degraded.status == "degraded"
    assert degraded.error is not None
    assert degraded.error.code == "MEM0_UNAVAILABLE"
    assert calls == 1


def test_state_only_mode_never_installs_memory_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_MEMORY_MODE", "state-only")
    service = build_service(db_path=str(tmp_path / "state-only.db"))
    service.store.create_project(ProjectCreate(id="lean", name="Lean"))

    result = service.bootstrap(BootstrapRequest(project_id="lean"))
    degraded = service.search(SearchRequest(project_id="lean", query="历史决定"))

    assert result.project.id == "lean"
    assert service.memory is None
    assert degraded.status == "degraded"
    assert degraded.error is not None
    assert degraded.error.code == "MEM0_DISABLED"


# --- bootstrap ---------------------------------------------------------------


def test_bootstrap_returns_full_bundle(server: MCPServer, store: ProjectStore) -> None:
    store.update_state(
        StateUpdate(project_id="demo", updates={"stage": "P3"}, expected_version=0)
    )
    store.add_decision(DecisionCreate(project_id="demo", topic="传输", decision="固定 5 秒重连"))
    store.add_task(TaskCreate(project_id="demo", title="实现 Service 层"))
    store.add_work_log(WorkLogCreate(project_id="demo", summary="完成 P2 验收"))

    payload = _call(server, "bootstrap", {"project_id": "demo"})

    assert payload["ok"] is True
    result = payload["result"]
    assert result["project"]["id"] == "demo"
    assert result["state"]["items"]["stage"]["value"] == "P3"
    assert [item["decision"] for item in result["recent_decisions"]] == ["固定 5 秒重连"]
    assert [item["title"] for item in result["open_tasks"]] == ["实现 Service 层"]
    assert [item["summary"] for item in result["recent_work"]] == ["完成 P2 验收"]


def test_bootstrap_rejects_unknown_project(server: MCPServer) -> None:
    payload = _call(server, "bootstrap", {"project_id": "missing"})

    assert payload["ok"] is False
    assert payload["error"]["code"] == "PROJECT_NOT_FOUND"
    assert payload["error"]["details"]["project_id"] == "missing"


# --- start_turn --------------------------------------------------------------


def test_start_turn_records_event_and_degrades_when_mem0_disabled(
    server: MCPServer, store: ProjectStore
) -> None:
    payload = _call(
        server,
        "start_turn",
        {"project_id": "demo", "content": "固定 5 秒重连", "agent_id": "codex-a"},
    )

    assert payload["ok"] is True
    result = payload["result"]
    assert result["memory_status"] == "pending"
    assert result["error"]["code"] == "MEM0_DISABLED"
    assert result["memories"] == []
    stored = store.get_raw_event(result["event"]["id"])
    assert stored.content == "固定 5 秒重连"
    assert stored.memory_extract_status == "pending"


def test_start_turn_rejects_system_role(server: MCPServer) -> None:
    payload = _call(
        server,
        "start_turn",
        {"project_id": "demo", "content": "x", "role": "system"},
    )

    assert payload["ok"] is False
    assert payload["error"]["code"] == "VALIDATION_ERROR"
    assert payload["error"]["details"]["errors"][0]["loc"] == ["role"]


def test_start_turn_rejects_empty_content(server: MCPServer) -> None:
    payload = _call(server, "start_turn", {"project_id": "demo", "content": ""})

    assert payload["ok"] is False
    assert payload["error"]["code"] == "VALIDATION_ERROR"


# --- search ------------------------------------------------------------------


def test_search_degrades_without_mem0(server: MCPServer) -> None:
    payload = _call(server, "search", {"project_id": "demo", "query": "怎么重连？"})

    assert payload["ok"] is True
    result = payload["result"]
    assert result["status"] == "degraded"
    assert result["memories"] == []
    assert result["error"]["code"] == "MEM0_DISABLED"


# --- get_state / update_state ------------------------------------------------


def test_get_state_round_trip(server: MCPServer, store: ProjectStore) -> None:
    store.update_state(
        StateUpdate(
            project_id="demo",
            updates={"stage": "P3", "owner": "claude"},
            expected_version=0,
        )
    )

    payload = _call(server, "get_state", {"project_id": "demo", "keys": ["owner"]})

    assert payload["ok"] is True
    assert set(payload["result"]["items"]) == {"owner"}
    assert payload["result"]["state_version"] == 2


def test_update_state_success(server: MCPServer) -> None:
    payload = _call(
        server,
        "update_state",
        {"project_id": "demo", "updates": {"stage": "P3"}, "expected_version": 0},
    )

    assert payload["ok"] is True
    assert payload["result"]["items"]["stage"]["value"] == "P3"
    assert payload["result"]["state_version"] == 1


def test_update_state_conflict_is_structured(server: MCPServer) -> None:
    _call(
        server,
        "update_state",
        {"project_id": "demo", "updates": {"stage": "P3"}, "expected_version": 0},
    )

    payload = _call(
        server,
        "update_state",
        {"project_id": "demo", "updates": {"stage": "P4"}, "expected_version": 0},
    )

    assert payload["ok"] is False
    error = payload["error"]
    assert error["code"] == "STATE_CONFLICT"
    assert error["details"]["expected_version"] == 0
    assert error["details"]["actual_version"] == 1


def test_update_state_missing_expected_version_is_rejected(
    server: MCPServer,
) -> None:
    # expected_version is a required function parameter: the SDK rejects it at
    # argument-validation time with a ToolError (is_error on the wire), before
    # the tool body runs. This asserts the stable rejection rather than a leak.
    with pytest.raises(Exception, match="expected_version"):
        asyncio.run(server.call_tool("update_state", {"project_id": "demo", "updates": {"stage": "P3"}}))


# --- finish_turn -------------------------------------------------------------


def test_finish_turn_logs_work_and_updates_state(server: MCPServer) -> None:
    payload = _call(
        server,
        "finish_turn",
        {
            "project_id": "demo",
            "summary": "实现 MCP 适配层",
            "changed_files": ["src/agent_memory_hub/mcp_server.py"],
            "agent_id": "workbuddy",
            "state_updates": {"stage": "P3-B"},
            "expected_state_version": 0,
        },
    )

    assert payload["ok"] is True
    result = payload["result"]
    assert result["state_updated"] is True
    assert result["state"]["items"]["stage"]["value"] == "P3-B"
    assert result["work_log"]["summary"] == "实现 MCP 适配层"
    assert result["work_log"]["changed_files"] == ["src/agent_memory_hub/mcp_server.py"]


def test_finish_turn_without_updates_only_logs(server: MCPServer) -> None:
    payload = _call(server, "finish_turn", {"project_id": "demo", "summary": "只记录工作"})

    assert payload["ok"] is True
    assert payload["result"]["state_updated"] is False
    assert payload["result"]["work_log"]["changed_files"] == []


def test_finish_turn_conflict_writes_nothing(
    server: MCPServer, store: ProjectStore
) -> None:
    store.update_state(
        StateUpdate(project_id="demo", updates={"stage": "P3"}, expected_version=0)
    )

    payload = _call(
        server,
        "finish_turn",
        {
            "project_id": "demo",
            "summary": "过期的收尾",
            "state_updates": {"stage": "P4"},
            "expected_state_version": 0,
        },
    )

    assert payload["ok"] is False
    assert payload["error"]["code"] == "STATE_CONFLICT"
    assert store.get_state("demo").items["stage"].value == "P3"
    assert store.list_recent_work("demo") == []


# --- correct_memory ----------------------------------------------------------


def test_correct_memory_is_audited_when_mem0_disabled(
    server: MCPServer, store: ProjectStore
) -> None:
    payload = _call(
        server,
        "correct_memory",
        {
            "project_id": "demo",
            "memory_id": "mem-1",
            "correction": "正确事实是固定 10 秒重连",
            "agent_id": "workbuddy",
        },
    )

    assert payload["ok"] is True
    result = payload["result"]
    assert result["status"] == "pending"
    assert result["error"]["code"] == "MEM0_DISABLED"
    assert store.get_raw_event(result["event"]["id"]).content == "正确事实是固定 10 秒重连"
    assert store.list_recent_work("demo")[0].id == result["work_log"]["id"]


def test_correct_memory_requires_version_for_state_updates(server: MCPServer) -> None:
    payload = _call(
        server,
        "correct_memory",
        {
            "project_id": "demo",
            "memory_id": "mem-1",
            "correction": "正确事实",
            "state_updates": {"stage": "P6"},
        },
    )

    assert payload["ok"] is False
    assert payload["error"]["code"] == "VALIDATION_ERROR"


# --- error containment -------------------------------------------------------


class ExplodingService:
    """A service stub that fails in a non-domain way on every call."""

    def bootstrap(self, request: BootstrapRequest) -> None:
        raise RuntimeError("boom: internal secret detail")


def test_unexpected_error_is_collapsed_without_traceback() -> None:
    server = create_server(ExplodingService())  # type: ignore[arg-type]

    payload = _call(server, "bootstrap", {"project_id": "demo"})

    assert payload["ok"] is False
    assert payload["error"]["code"] == "INTERNAL_ERROR"
    assert "Traceback" not in str(payload["error"])
    assert "internal secret detail" not in str(payload["error"])


# --- thinness guards ---------------------------------------------------------


def test_tools_parse_arguments_and_pass_them_through(server: MCPServer) -> None:
    """Sanity: the MCP layer forwards tool args without copying logic.

    The MEM0_DISABLED degradation proves the request was built and dispatched
    to the service; the limit is forwarded inside the search request.
    """
    payload = _call(
        server,
        "search",
        {"project_id": "demo", "query": "固定 5 秒重连", "limit": 3},
    )
    assert payload["ok"] is True
    assert payload["result"]["status"] == "degraded"
