from __future__ import annotations

from pathlib import Path

import pytest

from agent_memory_hub.errors import (
    MemoryEngineError,
    ProjectNotFoundError,
    StateConflictError,
)
from agent_memory_hub.project_store import ProjectStore
from agent_memory_hub.schemas import (
    BootstrapRequest,
    CorrectMemoryRequest,
    DecisionCreate,
    FinishTurnRequest,
    GetStateRequest,
    MemoryAddRequest,
    MemoryAddResult,
    MemoryCorrectRequest,
    MemoryCorrectResult,
    MemoryMutation,
    MemorySearchHit,
    MemorySearchRequest,
    MemorySearchResult,
    ProjectCreate,
    SearchRequest,
    StartTurnRequest,
    StateUpdate,
    TaskCreate,
    WorkLogCreate,
)
from agent_memory_hub.service import MemoryService


class FakeMemoryEngine:
    """In-process stand-in for MemoryEngine; no Qwen, Qdrant or embeddings."""

    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.add_requests: list[MemoryAddRequest] = []
        self.search_requests: list[MemorySearchRequest] = []
        self.correct_requests: list[MemoryCorrectRequest] = []

    def add(self, request: MemoryAddRequest) -> MemoryAddResult:
        self.add_requests.append(request)
        if self.failure is not None:
            raise self.failure
        return MemoryAddResult(
            memories=[MemoryMutation(memory_id="mem-1", memory=request.content, event="ADD")]
        )

    def search(self, request: MemorySearchRequest) -> MemorySearchResult:
        self.search_requests.append(request)
        if self.failure is not None:
            raise self.failure
        return MemorySearchResult(
            memories=[
                MemorySearchHit(
                    memory_id="mem-1",
                    memory="AGW 固定 5 秒重连",
                    score=0.9,
                    source_event_id="evt-1",
                )
            ]
        )

    def correct(self, request: MemoryCorrectRequest) -> MemoryCorrectResult:
        self.correct_requests.append(request)
        if self.failure is not None:
            raise self.failure
        return MemoryCorrectResult(memory_id=request.memory_id, action=request.action)


def mem0_down(operation: str = "add") -> MemoryEngineError:
    return MemoryEngineError(operation, ConnectionError("Qwen offline"))


@pytest.fixture
def store(tmp_path: Path) -> ProjectStore:
    result = ProjectStore(tmp_path / "project_state.db")
    result.initialize()
    result.create_project(ProjectCreate(id="demo", name="Demo", repo_path="/tmp/demo"))
    return result


@pytest.fixture
def engine() -> FakeMemoryEngine:
    return FakeMemoryEngine()


@pytest.fixture
def service(store: ProjectStore, engine: FakeMemoryEngine) -> MemoryService:
    return MemoryService(store, engine)


# --- bootstrap ---------------------------------------------------------------


def test_bootstrap_returns_state_decisions_tasks_and_recent_work(
    service: MemoryService, store: ProjectStore
) -> None:
    store.update_state(
        StateUpdate(project_id="demo", updates={"stage": "P3"}, expected_version=0)
    )
    store.add_decision(
        DecisionCreate(project_id="demo", topic="传输", decision="固定 5 秒重连")
    )
    store.add_task(TaskCreate(project_id="demo", title="实现 Service 层"))
    store.add_work_log(WorkLogCreate(project_id="demo", summary="完成 P2 验收"))

    result = service.bootstrap(BootstrapRequest(project_id="demo"))

    assert result.project.id == "demo"
    assert result.state.items["stage"].value == "P3"
    assert result.state.state_version == 1
    assert [item.decision for item in result.recent_decisions] == ["固定 5 秒重连"]
    assert [item.title for item in result.open_tasks] == ["实现 Service 层"]
    assert [item.summary for item in result.recent_work] == ["完成 P2 验收"]


def test_bootstrap_defaults_are_10_20_20(service: MemoryService) -> None:
    request = BootstrapRequest(project_id="demo")

    assert request.decision_limit == 10
    assert request.task_limit == 20
    assert request.work_limit == 20


def test_bootstrap_honours_limits_and_state_key_filter(
    service: MemoryService, store: ProjectStore
) -> None:
    store.update_state(
        StateUpdate(
            project_id="demo",
            updates={"stage": "P3", "owner": "claude"},
            expected_version=0,
        )
    )
    for index in range(3):
        store.add_task(TaskCreate(project_id="demo", title=f"task-{index}", priority=index))

    result = service.bootstrap(
        BootstrapRequest(project_id="demo", state_keys=["stage"], task_limit=2)
    )

    assert set(result.state.items) == {"stage"}
    assert result.state.state_version == 2
    assert [item.title for item in result.open_tasks] == ["task-0", "task-1"]


def test_bootstrap_works_while_mem0_is_unavailable(store: ProjectStore) -> None:
    service = MemoryService(store, FakeMemoryEngine(failure=mem0_down("search")))

    result = service.bootstrap(BootstrapRequest(project_id="demo"))

    assert result.project.id == "demo"
    assert result.state.state_version == 0


def test_bootstrap_rejects_unknown_project(service: MemoryService) -> None:
    with pytest.raises(ProjectNotFoundError) as failure:
        service.bootstrap(BootstrapRequest(project_id="missing"))
    assert failure.value.code == "PROJECT_NOT_FOUND"


# --- start_turn --------------------------------------------------------------


def test_start_turn_records_raw_event_before_extracting(
    service: MemoryService, engine: FakeMemoryEngine, store: ProjectStore
) -> None:
    result = service.start_turn(
        StartTurnRequest(
            project_id="demo",
            content="固定 5 秒重连",
            session_id="session-1",
            turn_id="turn-1",
            agent_id="codex-a",
        )
    )

    assert result.memory_status == "done"
    assert result.error is None
    assert [item.memory_id for item in result.memories] == ["mem-1"]

    add_request = engine.add_requests[0]
    assert add_request.event_id == result.event.id
    assert add_request.project_id == "demo"
    assert add_request.agent_id == "codex-a"
    assert add_request.source_role == "user"

    stored = store.get_raw_event(result.event.id)
    assert stored.memory_extract_status == "done"
    assert stored.memory_extract_error is None
    assert stored.turn_id == "turn-1"


def test_start_turn_keeps_raw_event_and_marks_pending_when_mem0_fails(
    store: ProjectStore,
) -> None:
    service = MemoryService(store, FakeMemoryEngine(failure=mem0_down("add")))

    result = service.start_turn(StartTurnRequest(project_id="demo", content="固定 5 秒重连"))

    assert result.memory_status == "pending"
    assert result.memories == []
    assert result.error is not None
    assert result.error.code == "MEM0_UNAVAILABLE"
    assert result.error.details["operation"] == "add"

    stored = store.get_raw_event(result.event.id)
    assert stored.content == "固定 5 秒重连"
    assert stored.memory_extract_status == "pending"
    assert stored.memory_extract_error is not None


def test_start_turn_without_memory_engine_degrades_but_still_records(
    store: ProjectStore,
) -> None:
    service = MemoryService(store, memory=None)

    result = service.start_turn(StartTurnRequest(project_id="demo", content="hello"))

    assert result.memory_status == "pending"
    assert result.error is not None
    assert result.error.code == "MEM0_DISABLED"
    assert store.get_raw_event(result.event.id).memory_extract_status == "pending"


def test_start_turn_rejects_unknown_project(service: MemoryService) -> None:
    with pytest.raises(ProjectNotFoundError):
        service.start_turn(StartTurnRequest(project_id="missing", content="hello"))


# --- search ------------------------------------------------------------------


def test_search_is_project_scoped_and_returns_hits(
    service: MemoryService, engine: FakeMemoryEngine
) -> None:
    result = service.search(SearchRequest(project_id="demo", query="怎么重连？", limit=5))

    assert result.status == "ok"
    assert result.error is None
    assert [item.memory_id for item in result.memories] == ["mem-1"]
    assert engine.search_requests[0].project_id == "demo"
    assert engine.search_requests[0].limit == 5


def test_search_degrades_instead_of_raising_when_mem0_fails(store: ProjectStore) -> None:
    service = MemoryService(store, FakeMemoryEngine(failure=mem0_down("search")))

    result = service.search(SearchRequest(project_id="demo", query="怎么重连？"))

    assert result.status == "degraded"
    assert result.memories == []
    assert result.error is not None
    assert result.error.code == "MEM0_UNAVAILABLE"


def test_search_without_memory_engine_is_degraded(store: ProjectStore) -> None:
    result = MemoryService(store, memory=None).search(
        SearchRequest(project_id="demo", query="怎么重连？")
    )

    assert result.status == "degraded"
    assert result.error is not None
    assert result.error.code == "MEM0_DISABLED"


# --- get_state / update_state ------------------------------------------------


def test_get_state_returns_snapshot_and_supports_key_filter(
    service: MemoryService, store: ProjectStore
) -> None:
    store.update_state(
        StateUpdate(
            project_id="demo",
            updates={"stage": "P3", "owner": "claude"},
            expected_version=0,
        )
    )

    everything = service.get_state(GetStateRequest(project_id="demo"))
    filtered = service.get_state(GetStateRequest(project_id="demo", keys=["owner"]))

    assert set(everything.items) == {"stage", "owner"}
    assert set(filtered.items) == {"owner"}
    assert filtered.state_version == 2


def test_update_state_applies_when_version_matches(service: MemoryService) -> None:
    snapshot = service.update_state(
        StateUpdate(project_id="demo", updates={"stage": "P3"}, expected_version=0)
    )

    assert snapshot.items["stage"].value == "P3"
    assert snapshot.state_version == 1


def test_update_state_raises_state_conflict_and_does_not_overwrite(
    service: MemoryService,
) -> None:
    service.update_state(
        StateUpdate(project_id="demo", updates={"stage": "P3"}, expected_version=0)
    )

    with pytest.raises(StateConflictError) as failure:
        service.update_state(
            StateUpdate(project_id="demo", updates={"stage": "P4"}, expected_version=0)
        )

    assert failure.value.code == "STATE_CONFLICT"
    assert failure.value.details == {
        "project_id": "demo",
        "expected_version": 0,
        "actual_version": 1,
    }
    assert service.get_state(GetStateRequest(project_id="demo")).items["stage"].value == "P3"


# --- finish_turn -------------------------------------------------------------


def test_finish_turn_records_work_log_and_updates_state(
    service: MemoryService, store: ProjectStore
) -> None:
    turn = service.start_turn(StartTurnRequest(project_id="demo", content="固定 5 秒重连"))

    result = service.finish_turn(
        FinishTurnRequest(
            project_id="demo",
            summary="实现 Service 编排层",
            changed_files=["src/agent_memory_hub/service.py"],
            session_id="session-1",
            agent_id="claude",
            commit_hash="abc1234",
            state_updates={"stage": "P3-A"},
            expected_state_version=0,
            source_event_id=turn.event.id,
        )
    )

    assert result.state_updated is True
    assert result.state.items["stage"].value == "P3-A"
    assert result.state.items["stage"].source_event_id == turn.event.id
    assert result.work_log.summary == "实现 Service 编排层"
    assert result.work_log.changed_files == ["src/agent_memory_hub/service.py"]
    assert result.work_log.commit_hash == "abc1234"
    assert [item.id for item in store.list_recent_work("demo")] == [result.work_log.id]


def test_finish_turn_without_state_updates_only_logs_work(service: MemoryService) -> None:
    result = service.finish_turn(FinishTurnRequest(project_id="demo", summary="只记录工作"))

    assert result.state_updated is False
    assert result.state.state_version == 0
    assert result.work_log.changed_files == []


def test_finish_turn_conflict_writes_neither_state_nor_work_log(
    service: MemoryService, store: ProjectStore
) -> None:
    service.update_state(
        StateUpdate(project_id="demo", updates={"stage": "P3"}, expected_version=0)
    )

    with pytest.raises(StateConflictError) as failure:
        service.finish_turn(
            FinishTurnRequest(
                project_id="demo",
                summary="过期的收尾",
                state_updates={"stage": "P4"},
                expected_state_version=0,
            )
        )

    assert failure.value.code == "STATE_CONFLICT"
    assert store.get_state("demo").items["stage"].value == "P3"
    assert store.list_recent_work("demo") == []


def test_finish_turn_requires_expected_version_for_state_updates() -> None:
    with pytest.raises(ValueError, match="expected_state_version"):
        FinishTurnRequest(project_id="demo", summary="缺少版本", state_updates={"stage": "P4"})


# --- correct_memory ----------------------------------------------------------


def test_correct_memory_is_audited_and_can_update_state(
    service: MemoryService, engine: FakeMemoryEngine, store: ProjectStore
) -> None:
    result = service.correct_memory(
        CorrectMemoryRequest(
            project_id="demo",
            memory_id="mem-1",
            correction="AGW 固定 10 秒重连",
            agent_id="codex-a",
            state_updates={"reconnect_interval": 10},
            expected_state_version=0,
        )
    )

    assert result.status == "done"
    assert result.event.memory_extract_status == "done"
    assert result.event.content == "AGW 固定 10 秒重连"
    assert result.work_log.summary.endswith(": done")
    assert result.state.items["reconnect_interval"].source_event_id == result.event.id
    assert engine.correct_requests[0].event_id == result.event.id


def test_correct_memory_failure_keeps_audit_as_pending(store: ProjectStore) -> None:
    service = MemoryService(store, FakeMemoryEngine(failure=mem0_down("correct")))

    result = service.correct_memory(
        CorrectMemoryRequest(
            project_id="demo",
            memory_id="mem-1",
            correction="正确事实",
        )
    )

    assert result.status == "pending"
    assert result.error is not None
    assert result.error.code == "MEM0_UNAVAILABLE"
    assert result.event.memory_extract_status == "pending"
    assert result.work_log.summary.endswith(": pending")


def test_correct_memory_conflict_does_not_touch_mem0_or_write_work_log(
    service: MemoryService, engine: FakeMemoryEngine, store: ProjectStore
) -> None:
    service.update_state(
        StateUpdate(project_id="demo", updates={"stage": "P5"}, expected_version=0)
    )

    with pytest.raises(StateConflictError):
        service.correct_memory(
            CorrectMemoryRequest(
                project_id="demo",
                memory_id="mem-1",
                correction="过期纠错",
                state_updates={"stage": "P6"},
                expected_state_version=0,
            )
        )

    assert engine.correct_requests == []
    assert store.list_recent_work("demo") == []
