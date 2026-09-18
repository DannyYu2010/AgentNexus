# ruff: noqa: BLE001 - MCP boundary must catch every exception so no traceback
# or internal message reaches the client; see the module docstring's error contract.
"""P3-B: stdio MCP adapter over :class:`~agent_memory_hub.service.MemoryService`.

This module is a thin transport layer. Each MCP tool only parses its arguments
into a Pydantic request, delegates to the service, and serializes the Pydantic
result with ``model_dump(mode="json")``. No database or Mem0 logic lives here;
the service stays the single owner of those workflows.

The server runs over stdio by default and never listens on a network port.
It follows the mcp 2.x SDK API (``MCPServer`` + ``@server.tool()``).

Error contract: a tool never raises toward the caller. Domain errors
(:class:`ProjectStoreError`, e.g. ``STATE_CONFLICT``) and argument-validation
failures are returned as structured JSON with a stable ``code`` and ``details``;
unexpected exceptions collapse to ``INTERNAL_ERROR``. No Python traceback ever
reaches the MCP client.
"""

from __future__ import annotations

from threading import Lock
from typing import Any

from mcp.server.mcpserver import MCPServer
from pydantic import ValidationError

from agent_memory_hub.config import MemorySettings
from agent_memory_hub.errors import MemoryEngineError, ProjectStoreError
from agent_memory_hub.memory_engine import create_memory_engine
from agent_memory_hub.project_store import ProjectStore
from agent_memory_hub.schemas import (
    BootstrapRequest,
    CorrectMemoryRequest,
    FinishTurnRequest,
    GetStateRequest,
    MemoryAddRequest,
    MemoryAddResult,
    MemoryCorrectRequest,
    MemoryCorrectResult,
    MemorySearchRequest,
    MemorySearchResult,
    SearchRequest,
    StartTurnRequest,
    StateUpdate,
)
from agent_memory_hub.service import MemoryService

SERVER_NAME = "agent-memory-hub"
SERVER_DESCRIPTION = (
    "Shared project-state and long-term memory for multi-agent coding sessions. "
    "Project State (SQLite) is authoritative; Mem0 long-term memory degrades "
    "gracefully when unavailable."
)

INTERNAL_ERROR_CODE = "INTERNAL_ERROR"


class LazyMemoryEngine:
    """Initialize Mem0 only when a memory operation actually needs it.

    Project-State-only tools such as ``bootstrap`` and ``get_state`` must never
    pay the embedding/Qdrant startup cost. The lock prevents concurrent first
    calls in one MCP process from initializing the heavy stack twice.
    """

    def __init__(self, settings: MemorySettings) -> None:
        self.settings = settings
        self._engine: Any = None
        self._lock = Lock()

    def add(self, request: MemoryAddRequest) -> MemoryAddResult:
        return self._resolve("add").add(request)

    def search(self, request: MemorySearchRequest) -> MemorySearchResult:
        return self._resolve("search").search(request)

    def correct(self, request: MemoryCorrectRequest) -> MemoryCorrectResult:
        return self._resolve("correct").correct(request)

    def _resolve(self, operation: str) -> Any:
        if self._engine is not None:
            return self._engine
        with self._lock:
            if self._engine is not None:
                return self._engine
            try:
                self._engine = create_memory_engine(self.settings)
            except Exception as exc:
                raise MemoryEngineError(f"initialize_for_{operation}", exc) from exc
        return self._engine


def build_service(
    db_path: str | None = None,
    memory: Any = None,
    *,
    use_memory: bool = True,
) -> MemoryService:
    """Assemble the real service from settings.

    The memory engine is heavyweight (Mem0 + Qdrant + embeddings), so this
    function installs a lazy proxy. Project-State-only calls never initialize
    Mem0; initialization failures become the service's structured degradation.
    """
    settings = MemorySettings()
    settings.ensure_data_directories()
    store = ProjectStore(db_path or settings.memory_data_dir / "project_state.db")
    store.initialize()
    if memory is not None:
        return MemoryService(store, memory)
    if use_memory and settings.memory_mode == "full":
        memory = LazyMemoryEngine(settings)
    return MemoryService(store, memory)


def create_server(service: MemoryService | None = None) -> MCPServer:
    """Build the stdio MCP server exposing the seven service workflows as tools.

    ``service`` is optional: when omitted, the real service is built lazily on
    the first tool call so importing this module stays cheap and the MCP CLI can
    list tools without loading Mem0/Qdrant/embeddings. Tests pass a fake service.
    """
    holder: dict[str, MemoryService] = {}
    if service is not None:
        holder["service"] = service

    def _resolve() -> MemoryService:
        if "service" not in holder:
            holder["service"] = build_service()
        return holder["service"]

    server = MCPServer(name=SERVER_NAME, description=SERVER_DESCRIPTION)

    @server.tool()
    def bootstrap(
        project_id: str,
        state_keys: list[str] | None = None,
        decision_limit: int = 10,
        task_limit: int = 20,
        work_limit: int = 20,
    ) -> dict[str, Any]:
        """Return everything an agent needs to resume work on a project.

        Reads only Project State, so it stays fully available while Mem0 is down.
        Defaults: 10 recent decisions, 20 open tasks, 20 recent work entries.
        """
        try:
            result = _resolve().bootstrap(
                BootstrapRequest(
                    project_id=project_id,
                    state_keys=state_keys,
                    decision_limit=decision_limit,
                    task_limit=task_limit,
                    work_limit=work_limit,
                )
            )
        except ProjectStoreError as exc:
            return _error_payload(exc)
        except ValidationError as exc:
            return _validation_payload(exc)
        except Exception as exc:
            return _unexpected_payload(exc)
        return _ok_payload(result)

    @server.tool()
    def start_turn(
        project_id: str,
        content: str,
        role: str = "user",
        session_id: str | None = None,
        turn_id: str | None = None,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist the turn's raw event, then try to extract long-term memories.

        Only ``user`` and ``assistant`` roles enter the Mem0 pipeline (``system``
        and ``tool`` roles are rejected by validation). When Mem0 is unavailable
        the raw event is kept as pending and the result carries a stable error
        code instead of raising.
        """
        try:
            result = _resolve().start_turn(
                StartTurnRequest(
                    project_id=project_id,
                    content=content,
                    role=role,
                    session_id=session_id,
                    turn_id=turn_id,
                    agent_id=agent_id,
                )
            )
        except ProjectStoreError as exc:
            return _error_payload(exc)
        except ValidationError as exc:
            return _validation_payload(exc)
        except Exception as exc:
            return _unexpected_payload(exc)
        return _ok_payload(result)

    @server.tool()
    def search(project_id: str, query: str, limit: int = 8) -> dict[str, Any]:
        """Search project-scoped long-term memory; degrades instead of raising."""
        try:
            result = _resolve().search(
                SearchRequest(project_id=project_id, query=query, limit=limit)
            )
        except ProjectStoreError as exc:
            return _error_payload(exc)
        except ValidationError as exc:
            return _validation_payload(exc)
        except Exception as exc:
            return _unexpected_payload(exc)
        return _ok_payload(result)

    @server.tool()
    def get_state(project_id: str, keys: list[str] | None = None) -> dict[str, Any]:
        """Read the authoritative Project State snapshot, optionally filtered."""
        try:
            result = _resolve().get_state(GetStateRequest(project_id=project_id, keys=keys))
        except ProjectStoreError as exc:
            return _error_payload(exc)
        except ValidationError as exc:
            return _validation_payload(exc)
        except Exception as exc:
            return _unexpected_payload(exc)
        return _ok_payload(result)

    @server.tool()
    def update_state(
        project_id: str,
        updates: dict[str, Any],
        expected_version: int,
        source_event_id: str | None = None,
    ) -> dict[str, Any]:
        """Apply an optimistic-locked state update.

        A version mismatch returns ``STATE_CONFLICT`` with the expected and
        actual versions; the update is never silently overwritten.
        """
        try:
            result = _resolve().update_state(
                StateUpdate(
                    project_id=project_id,
                    updates=updates,
                    expected_version=expected_version,
                    source_event_id=source_event_id,
                )
            )
        except ProjectStoreError as exc:
            return _error_payload(exc)
        except ValidationError as exc:
            return _validation_payload(exc)
        except Exception as exc:
            return _unexpected_payload(exc)
        return _ok_payload(result)

    @server.tool()
    def finish_turn(
        project_id: str,
        summary: str,
        changed_files: list[str] | None = None,
        session_id: str | None = None,
        agent_id: str | None = None,
        commit_hash: str | None = None,
        state_updates: dict[str, Any] | None = None,
        expected_state_version: int | None = None,
        source_event_id: str | None = None,
    ) -> dict[str, Any]:
        """Close a turn: apply the optimistic state update, then log the work.

        ``expected_state_version`` is required when ``state_updates`` is set; a
        mismatch returns ``STATE_CONFLICT`` and neither the state nor the work
        log is written.
        """
        try:
            result = _resolve().finish_turn(
                FinishTurnRequest(
                    project_id=project_id,
                    summary=summary,
                    changed_files=list(changed_files or []),
                    session_id=session_id,
                    agent_id=agent_id,
                    commit_hash=commit_hash,
                    state_updates=state_updates or {},
                    expected_state_version=expected_state_version,
                    source_event_id=source_event_id,
                )
            )
        except ProjectStoreError as exc:
            return _error_payload(exc)
        except ValidationError as exc:
            return _validation_payload(exc)
        except Exception as exc:
            return _unexpected_payload(exc)
        return _ok_payload(result)

    @server.tool()
    def correct_memory(
        project_id: str,
        memory_id: str,
        correction: str,
        action: str = "update",
        session_id: str | None = None,
        agent_id: str | None = None,
        state_updates: dict[str, Any] | None = None,
        expected_state_version: int | None = None,
    ) -> dict[str, Any]:
        """Correct a project-scoped memory with raw-event and work-log audit records.

        ``action=update`` replaces the memory text while Mem0 retains its history.
        ``action=delete`` removes it and writes ``correction`` as an explicit
        replacement memory. Optional Project State changes remain optimistic-locked.
        """
        try:
            result = _resolve().correct_memory(
                CorrectMemoryRequest(
                    project_id=project_id,
                    memory_id=memory_id,
                    correction=correction,
                    action=action,
                    session_id=session_id,
                    agent_id=agent_id,
                    state_updates=state_updates or {},
                    expected_state_version=expected_state_version,
                )
            )
        except ProjectStoreError as exc:
            return _error_payload(exc)
        except ValidationError as exc:
            return _validation_payload(exc)
        except Exception as exc:
            return _unexpected_payload(exc)
        return _ok_payload(result)

    return server


def _ok_payload(result: Any) -> dict[str, Any]:
    """Wrap a successful Pydantic result as stable JSON."""
    return {"ok": True, "result": result.model_dump(mode="json")}


def _error_payload(error: ProjectStoreError) -> dict[str, Any]:
    """Stable, serializable form of a domain error (no traceback)."""
    payload = error.as_dict()
    details = {key: value for key, value in payload.get("details", {}).items() if value is not None}
    return {"ok": False, "error": {"code": payload["code"], "message": payload["message"], "details": details}}


def _validation_payload(error: ValidationError) -> dict[str, Any]:
    """Argument/field validation failure with pydantic's loc+msg, no traceback."""
    errors = [
        {"loc": list(item["loc"]), "message": item["msg"], "type": item["type"]}
        for item in error.errors()
    ]
    return {
        "ok": False,
        "error": {"code": "VALIDATION_ERROR", "message": str(error), "details": {"errors": errors}},
    }


def _unexpected_payload(error: Exception) -> dict[str, Any]:
    """Collapse any other failure; never leak the traceback or its message.

    The original exception text may embed internal details (paths, SQL, keys),
    so the caller only receives a stable code and a generic message.
    """
    return {
        "ok": False,
        "error": {
            "code": INTERNAL_ERROR_CODE,
            "message": "Unexpected internal error",
            "details": {},
        },
    }


# Module-level instance for the `mcp run` / `mcp dev` CLI, which imports this
# module and looks for a variable named `mcp`, `server` or `app`. The real
# service stays lazy: nothing heavy (Mem0/Qdrant/embeddings) is built at import
# time, only on the first tool call.
mcp = create_server()


def main() -> None:
    """Entry point: run the stdio server with a real service."""
    mcp.run()


if __name__ == "__main__":
    main()
