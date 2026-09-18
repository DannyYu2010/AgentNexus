from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_memory_hub.errors import (
    ProjectAlreadyExistsError,
    ProjectNotFoundError,
    RecordNotFoundError,
    StateConflictError,
)
from agent_memory_hub.project_store import ProjectStore
from agent_memory_hub.schemas import (
    DecisionCreate,
    ProjectCreate,
    RawEventCreate,
    StateUpdate,
    TaskCreate,
    WorkLogCreate,
)


@pytest.fixture
def store(tmp_path: Path) -> ProjectStore:
    result = ProjectStore(tmp_path / "project_state.db")
    result.initialize()
    result.create_project(
        ProjectCreate(id="demo", name="Demo", repo_path="/tmp/demo")
    )
    return result


def test_initialize_sets_schema_wal_foreign_keys_and_busy_timeout(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    store = ProjectStore(db_path)
    store.initialize()
    store.initialize()

    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "projects",
            "raw_events",
            "state_items",
            "tasks",
            "decisions",
            "work_logs",
        } <= tables
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"

    with store._connect() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_projects_are_typed_and_duplicates_are_rejected(store: ProjectStore) -> None:
    project = store.get_project("demo")
    assert project.repo_path == "/tmp/demo"

    with pytest.raises(ProjectAlreadyExistsError) as duplicate:
        store.create_project(ProjectCreate(id="demo", name="Again"))
    assert duplicate.value.code == "PROJECT_ALREADY_EXISTS"

    with pytest.raises(ProjectNotFoundError):
        store.get_project("missing")


def test_raw_event_preserves_original_text_and_extract_status(store: ProjectStore) -> None:
    event = store.record_raw_event(
        RawEventCreate(
            project_id="demo",
            session_id="session-a",
            turn_id="turn-1",
            agent_id="codex-a",
            role="user",
            content="以后重连固定 5 秒。",
        )
    )
    assert event.content == "以后重连固定 5 秒。"
    assert event.memory_extract_status == "pending"

    failed = store.mark_extract_pending(event.id, "Qwen unavailable")
    assert failed.memory_extract_error == "Qwen unavailable"

    done = store.mark_extract_done(event.id)
    assert done.memory_extract_status == "done"
    assert done.memory_extract_error is None
    assert store.get_raw_event(event.id) == done

    with pytest.raises(RecordNotFoundError):
        store.mark_extract_done("evt_missing")


def test_state_update_is_atomic_versioned_and_filterable(store: ProjectStore) -> None:
    empty = store.get_state("demo")
    assert empty.items == {}
    assert empty.state_version == 0

    first = store.update_state(
        StateUpdate(
            project_id="demo",
            updates={"agw.reconnect": True, "agw.interval": 5},
            expected_version=0,
        )
    )
    assert first.state_version == 2
    assert first.items["agw.interval"].version == 1

    second = store.update_state(
        StateUpdate(
            project_id="demo",
            updates={"agw.interval": 10},
            expected_version=2,
        )
    )
    assert second.state_version == 3
    assert second.items["agw.interval"].value == 10
    assert second.items["agw.interval"].version == 2

    filtered = store.get_state("demo", keys=["agw.interval"])
    assert list(filtered.items) == ["agw.interval"]
    assert filtered.state_version == second.state_version

    with pytest.raises(StateConflictError) as conflict:
        store.update_state(
            StateUpdate(
                project_id="demo",
                updates={"agw.interval": 15},
                expected_version=2,
            )
        )
    assert conflict.value.details["actual_version"] == 3
    assert store.get_state("demo").items["agw.interval"].value == 10


def test_tasks_can_be_prioritized_listed_and_completed(store: ProjectStore) -> None:
    low = store.add_task(TaskCreate(project_id="demo", title="Later", priority=80))
    high = store.add_task(TaskCreate(project_id="demo", title="Now", priority=10))

    assert [task.id for task in store.list_open_tasks("demo")] == [high.id, low.id]
    completed = store.complete_task("demo", high.id)
    assert completed.status == "completed"
    assert [task.id for task in store.list_open_tasks("demo")] == [low.id]

    with pytest.raises(RecordNotFoundError):
        store.complete_task("demo", "task_missing")


def test_new_decision_supersedes_active_decision_with_same_topic(
    store: ProjectStore,
) -> None:
    first = store.add_decision(
        DecisionCreate(project_id="demo", topic="reconnect", decision="5 seconds")
    )
    unrelated = store.add_decision(
        DecisionCreate(project_id="demo", topic="backoff", decision="disabled")
    )
    second = store.add_decision(
        DecisionCreate(project_id="demo", topic="reconnect", decision="10 seconds")
    )

    decisions = {item.id: item for item in store.list_recent_decisions("demo")}
    assert decisions[first.id].status == "superseded"
    assert decisions[first.id].superseded_by == second.id
    assert decisions[second.id].status == "active"
    assert decisions[unrelated.id].status == "active"


def test_work_logs_round_trip_changed_files_and_persist_across_instances(
    store: ProjectStore,
) -> None:
    created = store.add_work_log(
        WorkLogCreate(
            project_id="demo",
            session_id="session-a",
            agent_id="codex-a",
            summary="Implemented reconnect",
            changed_files=["src/reconnect.py", "tests/test_reconnect.py"],
            commit_hash="abc123",
        )
    )
    assert created.changed_files == ["src/reconnect.py", "tests/test_reconnect.py"]

    reopened = ProjectStore(store.db_path)
    listed = reopened.list_recent_work("demo")
    assert listed == [created]


def test_writes_require_an_existing_project(store: ProjectStore) -> None:
    with pytest.raises(ProjectNotFoundError):
        store.record_raw_event(
            RawEventCreate(project_id="missing", role="user", content="hello")
        )
    with pytest.raises(ProjectNotFoundError):
        store.get_state("missing")
