"""Memory Service: the orchestration layer between storage and callers.

This layer owns every workflow that needs both the authoritative Project State
(SQLite) and long-term memory (Mem0). It never talks to SQLite or Mem0 directly;
it composes :class:`~agent_memory_hub.project_store.ProjectStore` and
:class:`~agent_memory_hub.memory_engine.MemoryEngine` and keeps their semantics
unchanged.

Degradation contract: Project State is authoritative and must keep working when
Mem0 is unavailable. Mem0 failures therefore never propagate out of
``start_turn`` or ``search``; they are reported as structured, identifiable
degraded results while the raw event stays durably recorded and ``pending``.
"""

from __future__ import annotations

from typing import Protocol

from .errors import MemoryEngineError, ProjectStoreError
from .project_store import ProjectStore
from .schemas import (
    BootstrapRequest,
    BootstrapResult,
    CorrectMemoryRequest,
    CorrectMemoryResult,
    ErrorInfo,
    FinishTurnRequest,
    FinishTurnResult,
    GetStateRequest,
    MemoryAddRequest,
    MemoryAddResult,
    MemoryCorrectRequest,
    MemoryCorrectResult,
    MemorySearchRequest,
    MemorySearchResult,
    RawEventCreate,
    SearchRequest,
    SearchResult,
    StartTurnRequest,
    StartTurnResult,
    StateSnapshot,
    StateUpdate,
    WorkLogCreate,
)

MEMORY_DISABLED_CODE = "MEM0_DISABLED"


class SupportsMemory(Protocol):
    """The subset of MemoryEngine the service depends on."""

    def add(self, request: MemoryAddRequest) -> MemoryAddResult: ...

    def search(self, request: MemorySearchRequest) -> MemorySearchResult: ...

    def correct(self, request: MemoryCorrectRequest) -> MemoryCorrectResult: ...


class MemoryService:
    """Structured, Pydantic-typed workflows over Project State and Mem0."""

    def __init__(self, store: ProjectStore, memory: SupportsMemory | None = None) -> None:
        self.store = store
        self.memory = memory

    def bootstrap(self, request: BootstrapRequest) -> BootstrapResult:
        """Return everything an agent needs to resume work on a project.

        Reads only Project State, so it stays fully available while Mem0 is down.
        """
        project = self.store.get_project(request.project_id)
        return BootstrapResult(
            project=project,
            state=self.store.get_state(request.project_id, request.state_keys),
            recent_decisions=self.store.list_recent_decisions(
                request.project_id, limit=request.decision_limit
            ),
            open_tasks=self.store.list_open_tasks(request.project_id, limit=request.task_limit),
            recent_work=self.store.list_recent_work(request.project_id, limit=request.work_limit),
        )

    def start_turn(self, request: StartTurnRequest) -> StartTurnResult:
        """Persist the turn's raw event first, then try to extract memories.

        The raw event is always durable. When Mem0 fails the event is kept and
        left in ``pending`` with the failure recorded, and the caller receives a
        degraded result carrying a stable error code instead of an exception.
        """
        event = self.store.record_raw_event(
            RawEventCreate(
                project_id=request.project_id,
                role=request.role,
                content=request.content,
                session_id=request.session_id,
                turn_id=request.turn_id,
                agent_id=request.agent_id,
            )
        )

        if self.memory is None:
            error = ErrorInfo(
                code=MEMORY_DISABLED_CODE,
                message="Memory engine is not configured; raw event kept as pending",
                details={"event_id": event.id},
            )
            event = self.store.mark_extract_pending(event.id, error.message)
            return StartTurnResult(event=event, memory_status="pending", error=error)

        try:
            added = self.memory.add(
                MemoryAddRequest(
                    project_id=request.project_id,
                    content=request.content,
                    event_id=event.id,
                    session_id=request.session_id,
                    agent_id=request.agent_id,
                    source_role=request.role,
                )
            )
        except MemoryEngineError as exc:
            error = _error_info(exc)
            event = self.store.mark_extract_pending(event.id, error.message)
            return StartTurnResult(event=event, memory_status="pending", error=error)

        event = self.store.mark_extract_done(event.id)
        return StartTurnResult(
            event=event, memory_status="done", memories=list(added.memories)
        )

    def search(self, request: SearchRequest) -> SearchResult:
        """Search project-scoped long-term memory, degrading instead of raising."""
        if self.memory is None:
            return SearchResult(
                status="degraded",
                error=ErrorInfo(
                    code=MEMORY_DISABLED_CODE,
                    message="Memory engine is not configured; no memories can be searched",
                    details={"project_id": request.project_id},
                ),
            )
        try:
            found = self.memory.search(
                MemorySearchRequest(
                    project_id=request.project_id,
                    query=request.query,
                    limit=request.limit,
                )
            )
        except MemoryEngineError as exc:
            return SearchResult(status="degraded", error=_error_info(exc))
        return SearchResult(status="ok", memories=list(found.memories))

    def get_state(self, request: GetStateRequest) -> StateSnapshot:
        """Read the authoritative Project State snapshot."""
        return self.store.get_state(request.project_id, request.keys)

    def update_state(self, request: StateUpdate) -> StateSnapshot:
        """Apply an optimistic-locked state update.

        A version mismatch raises :class:`StateConflictError` (``STATE_CONFLICT``);
        the service never retries or silently overwrites on the caller's behalf.
        """
        return self.store.update_state(request)

    def finish_turn(self, request: FinishTurnRequest) -> FinishTurnResult:
        """Close a turn: apply the optimistic state update, then log the work.

        The state update runs first on purpose. ``STATE_CONFLICT`` is a
        recoverable error the caller is expected to retry after re-reading the
        version, and a work log written before the conflict would be duplicated
        by that retry.
        """
        state_updated = False
        expected_version = request.expected_state_version
        if request.state_updates and expected_version is not None:
            state = self.store.update_state(
                StateUpdate(
                    project_id=request.project_id,
                    updates=request.state_updates,
                    expected_version=expected_version,
                    source_event_id=request.source_event_id,
                )
            )
            state_updated = True
        else:
            state = self.store.get_state(request.project_id)

        work_log = self.store.add_work_log(
            WorkLogCreate(
                project_id=request.project_id,
                summary=request.summary,
                changed_files=list(request.changed_files),
                session_id=request.session_id,
                agent_id=request.agent_id,
                commit_hash=request.commit_hash,
            )
        )
        return FinishTurnResult(work_log=work_log, state=state, state_updated=state_updated)

    def correct_memory(self, request: CorrectMemoryRequest) -> CorrectMemoryResult:
        """Apply an audited memory correction and optional authoritative state update."""
        event = self.store.record_raw_event(
            RawEventCreate(
                project_id=request.project_id,
                role="user",
                content=request.correction,
                session_id=request.session_id,
                agent_id=request.agent_id,
            )
        )

        state_updated = False
        try:
            if request.state_updates and request.expected_state_version is not None:
                state = self.store.update_state(
                    StateUpdate(
                        project_id=request.project_id,
                        updates=request.state_updates,
                        expected_version=request.expected_state_version,
                        source_event_id=event.id,
                    )
                )
                state_updated = True
            else:
                state = self.store.get_state(request.project_id)
        except ProjectStoreError as exc:
            self.store.mark_extract_pending(event.id, str(exc))
            raise

        mutation = None
        error = None
        status = "done"
        if self.memory is None:
            status = "pending"
            error = ErrorInfo(
                code=MEMORY_DISABLED_CODE,
                message="Memory engine is not configured; correction kept as pending",
                details={"event_id": event.id, "memory_id": request.memory_id},
            )
            event = self.store.mark_extract_pending(event.id, error.message)
        else:
            try:
                mutation = self.memory.correct(
                    MemoryCorrectRequest(
                        project_id=request.project_id,
                        memory_id=request.memory_id,
                        correction=request.correction,
                        event_id=event.id,
                        action=request.action,
                    )
                )
            except MemoryEngineError as exc:
                status = "pending"
                error = _error_info(exc)
                event = self.store.mark_extract_pending(event.id, error.message)
            else:
                event = self.store.mark_extract_done(event.id)

        work_log = self.store.add_work_log(
            WorkLogCreate(
                project_id=request.project_id,
                session_id=request.session_id,
                agent_id=request.agent_id,
                summary=(
                    f"Memory correction {request.action} {request.memory_id}: {status}"
                ),
            )
        )
        return CorrectMemoryResult(
            event=event,
            work_log=work_log,
            state=state,
            state_updated=state_updated,
            status=status,
            mutation=mutation,
            error=error,
        )


def _error_info(error: MemoryEngineError | ProjectStoreError) -> ErrorInfo:
    """Normalize a domain error into the service's serializable error shape."""
    payload = error.as_dict()
    details = {
        key: value for key, value in payload.get("details", {}).items() if value is not None
    }
    return ErrorInfo(code=payload["code"], message=payload["message"], details=details)
