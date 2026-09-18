"""SQLite-backed authoritative project state for the P0 milestone."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .errors import (
    ProjectAlreadyExistsError,
    ProjectNotFoundError,
    RecordNotFoundError,
    StateConflictError,
)
from .schemas import (
    Decision,
    DecisionCreate,
    Project,
    ProjectCreate,
    RawEvent,
    RawEventCreate,
    StateItem,
    StateSnapshot,
    StateUpdate,
    Task,
    TaskCreate,
    WorkLog,
    WorkLogCreate,
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class ProjectStore:
    """Owns the Project State database and its transactional invariants."""

    def __init__(self, db_path: str | Path, migration_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path)
        self.migration_path = Path(migration_path) if migration_path else self._default_migration()

    @staticmethod
    def _default_migration() -> Path:
        return Path(__file__).resolve().parents[2] / "migrations" / "001_init.sql"

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create or upgrade the P0 schema idempotently."""
        sql = self.migration_path.read_text(encoding="utf-8")
        with self._connect() as connection:
            connection.executescript(sql)

    def create_project(self, request: ProjectCreate) -> Project:
        now = _now()
        with self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO projects(id, name, repo_path, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (request.id, request.name, request.repo_path, now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise ProjectAlreadyExistsError(request.id) from exc
        return self.get_project(request.id)

    def get_project(self, project_id: str) -> Project:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            raise ProjectNotFoundError(project_id)
        return Project.model_validate(dict(row))

    def record_raw_event(self, request: RawEventCreate) -> RawEvent:
        event_id = _id("evt")
        created_at = _now()
        with self._connect() as connection:
            self._require_project(connection, request.project_id)
            connection.execute(
                """INSERT INTO raw_events(
                    id, project_id, session_id, turn_id, agent_id, role, content, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event_id,
                    request.project_id,
                    request.session_id,
                    request.turn_id,
                    request.agent_id,
                    request.role,
                    request.content,
                    created_at,
                ),
            )
        return self.get_raw_event(event_id)

    def get_raw_event(self, event_id: str) -> RawEvent:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM raw_events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            raise RecordNotFoundError("raw_event", event_id)
        return RawEvent.model_validate(dict(row))

    def mark_extract_done(self, event_id: str) -> RawEvent:
        return self._set_extract_status(event_id, "done", None)

    def mark_extract_pending(self, event_id: str, error: str | None = None) -> RawEvent:
        return self._set_extract_status(event_id, "pending", error)

    def _set_extract_status(self, event_id: str, status: str, error: str | None) -> RawEvent:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE raw_events SET memory_extract_status = ?, memory_extract_error = ? "
                "WHERE id = ?",
                (status, error, event_id),
            )
        if cursor.rowcount != 1:
            raise RecordNotFoundError("raw_event", event_id)
        return self.get_raw_event(event_id)

    def get_state(self, project_id: str, keys: Sequence[str] | None = None) -> StateSnapshot:
        with self._connect() as connection:
            self._require_project(connection, project_id)
            state_version = self._state_version(connection, project_id)
            if keys is None:
                rows = connection.execute(
                    "SELECT * FROM state_items WHERE project_id = ? ORDER BY key", (project_id,)
                ).fetchall()
            elif not keys:
                rows = []
            else:
                placeholders = ",".join("?" for _ in keys)
                rows = connection.execute(
                    f"SELECT * FROM state_items WHERE project_id = ? "
                    f"AND key IN ({placeholders}) ORDER BY key",
                    (project_id, *keys),
                ).fetchall()
        items = {row["key"]: self._state_item(row) for row in rows}
        return StateSnapshot(project_id=project_id, items=items, state_version=state_version)

    def update_state(self, request: StateUpdate) -> StateSnapshot:
        """Atomically apply an update when the caller's project state version matches."""
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_project(connection, request.project_id)
                actual_version = self._state_version(connection, request.project_id)
                if actual_version != request.expected_version:
                    raise StateConflictError(
                        request.project_id, request.expected_version, actual_version
                    )
                for key, value in request.updates.items():
                    value_json = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                    connection.execute(
                        """INSERT INTO state_items(
                            project_id, key, value_json, version, source_event_id, updated_at
                        ) VALUES (?, ?, ?, 1, ?, ?)
                        ON CONFLICT(project_id, key) DO UPDATE SET
                            value_json = excluded.value_json,
                            version = state_items.version + 1,
                            source_event_id = excluded.source_event_id,
                            updated_at = excluded.updated_at""",
                        (request.project_id, key, value_json, request.source_event_id, now),
                    )
                connection.execute(
                    "UPDATE projects SET updated_at = ? WHERE id = ?", (now, request.project_id)
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self.get_state(request.project_id)

    def add_task(self, request: TaskCreate) -> Task:
        task_id = _id("task")
        now = _now()
        with self._connect() as connection:
            self._require_project(connection, request.project_id)
            connection.execute(
                """INSERT INTO tasks(
                    id, project_id, title, status, priority, source_event_id, created_at, updated_at
                ) VALUES (?, ?, ?, 'open', ?, ?, ?, ?)""",
                (
                    task_id,
                    request.project_id,
                    request.title,
                    request.priority,
                    request.source_event_id,
                    now,
                    now,
                ),
            )
        return self._get_task(task_id)

    def list_open_tasks(self, project_id: str, limit: int = 20) -> list[Task]:
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._connect() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                """SELECT * FROM tasks
                WHERE project_id = ? AND status IN ('open', 'in_progress')
                ORDER BY priority ASC, created_at ASC LIMIT ?""",
                (project_id, limit),
            ).fetchall()
        return [Task.model_validate(dict(row)) for row in rows]

    def complete_task(self, project_id: str, task_id: str) -> Task:
        now = _now()
        with self._connect() as connection:
            self._require_project(connection, project_id)
            cursor = connection.execute(
                "UPDATE tasks SET status = 'completed', updated_at = ? "
                "WHERE id = ? AND project_id = ?",
                (now, task_id, project_id),
            )
        if cursor.rowcount != 1:
            raise RecordNotFoundError("task", task_id)
        return self._get_task(task_id)

    def _get_task(self, task_id: str) -> Task:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise RecordNotFoundError("task", task_id)
        return Task.model_validate(dict(row))

    def add_decision(self, request: DecisionCreate) -> Decision:
        decision_id = _id("decision")
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_project(connection, request.project_id)
                connection.execute(
                    """INSERT INTO decisions(
                        id, project_id, topic, decision, status, source_event_id, created_at
                    ) VALUES (?, ?, ?, ?, 'active', ?, ?)""",
                    (
                        decision_id,
                        request.project_id,
                        request.topic,
                        request.decision,
                        request.source_event_id,
                        now,
                    ),
                )
                connection.execute(
                    """UPDATE decisions SET status = 'superseded', superseded_by = ?
                    WHERE project_id = ? AND topic = ? AND status = 'active' AND id != ?""",
                    (decision_id, request.project_id, request.topic, decision_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self._get_decision(decision_id)

    def list_recent_decisions(self, project_id: str, limit: int = 10) -> list[Decision]:
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._connect() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                "SELECT * FROM decisions WHERE project_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        return [Decision.model_validate(dict(row)) for row in rows]

    def _get_decision(self, decision_id: str) -> Decision:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM decisions WHERE id = ?", (decision_id,)
            ).fetchone()
        if row is None:
            raise RecordNotFoundError("decision", decision_id)
        return Decision.model_validate(dict(row))

    def add_work_log(self, request: WorkLogCreate) -> WorkLog:
        work_log_id = _id("work")
        created_at = _now()
        changed_files_json = json.dumps(
            request.changed_files, ensure_ascii=False, separators=(",", ":")
        )
        with self._connect() as connection:
            self._require_project(connection, request.project_id)
            connection.execute(
                """INSERT INTO work_logs(
                    id, project_id, session_id, agent_id, summary, changed_files_json,
                    commit_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    work_log_id,
                    request.project_id,
                    request.session_id,
                    request.agent_id,
                    request.summary,
                    changed_files_json,
                    request.commit_hash,
                    created_at,
                ),
            )
        return self._get_work_log(work_log_id)

    def list_recent_work(self, project_id: str, limit: int = 20) -> list[WorkLog]:
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._connect() as connection:
            self._require_project(connection, project_id)
            rows = connection.execute(
                "SELECT * FROM work_logs WHERE project_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        return [self._work_log(row) for row in rows]

    def _get_work_log(self, work_log_id: str) -> WorkLog:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM work_logs WHERE id = ?", (work_log_id,)).fetchone()
        if row is None:
            raise RecordNotFoundError("work_log", work_log_id)
        return self._work_log(row)

    @staticmethod
    def _state_item(row: sqlite3.Row) -> StateItem:
        return StateItem(
            key=row["key"],
            value=json.loads(row["value_json"]),
            version=row["version"],
            source_event_id=row["source_event_id"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _work_log(row: sqlite3.Row) -> WorkLog:
        data = dict(row)
        data["changed_files"] = json.loads(data.pop("changed_files_json"))
        return WorkLog.model_validate(data)

    @staticmethod
    def _state_version(connection: sqlite3.Connection, project_id: str) -> int:
        row = connection.execute(
            "SELECT COALESCE(SUM(version), 0) AS version FROM state_items WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        return int(row["version"])

    @staticmethod
    def _require_project(connection: sqlite3.Connection, project_id: str) -> None:
        row = connection.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone()
        if row is None:
            raise ProjectNotFoundError(project_id)
