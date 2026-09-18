"""Pydantic contracts for Project Store inputs and outputs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectCreate(Schema):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    repo_path: str | None = None


class Project(Schema):
    id: str
    name: str
    repo_path: str | None
    created_at: datetime
    updated_at: datetime


class RawEventCreate(Schema):
    project_id: str = Field(min_length=1)
    role: Literal["user", "assistant", "system", "tool"]
    content: str = Field(min_length=1)
    session_id: str | None = None
    turn_id: str | None = None
    agent_id: str | None = None


class RawEvent(Schema):
    id: str
    project_id: str
    session_id: str | None
    turn_id: str | None
    agent_id: str | None
    role: str
    content: str
    memory_extract_status: Literal["pending", "done"]
    memory_extract_error: str | None
    created_at: datetime


class StateUpdate(Schema):
    project_id: str = Field(min_length=1)
    updates: dict[str, JsonValue] = Field(min_length=1)
    expected_version: int = Field(ge=0)
    source_event_id: str | None = None


class StateItem(Schema):
    key: str
    value: JsonValue
    version: int
    source_event_id: str | None
    updated_at: datetime


class StateSnapshot(Schema):
    project_id: str
    items: dict[str, StateItem]
    state_version: int


class TaskCreate(Schema):
    project_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    priority: int = Field(default=50, ge=0, le=100)
    source_event_id: str | None = None


class Task(Schema):
    id: str
    project_id: str
    title: str
    status: Literal["open", "in_progress", "completed", "cancelled"]
    priority: int
    source_event_id: str | None
    created_at: datetime
    updated_at: datetime


class DecisionCreate(Schema):
    project_id: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    decision: str = Field(min_length=1)
    source_event_id: str | None = None


class Decision(Schema):
    id: str
    project_id: str
    topic: str
    decision: str
    status: Literal["active", "superseded"]
    superseded_by: str | None
    source_event_id: str | None
    created_at: datetime


class WorkLogCreate(Schema):
    project_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    changed_files: list[str] = Field(default_factory=list)
    session_id: str | None = None
    agent_id: str | None = None
    commit_hash: str | None = None


class WorkLog(Schema):
    id: str
    project_id: str
    session_id: str | None
    agent_id: str | None
    summary: str
    changed_files: list[str]
    commit_hash: str | None
    created_at: datetime


class MemoryAddRequest(Schema):
    project_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    session_id: str | None = None
    agent_id: str | None = None
    source_role: Literal["user", "assistant"] = "user"


class MemoryMutation(Schema):
    memory_id: str
    memory: str
    event: str


class MemoryAddResult(Schema):
    memories: list[MemoryMutation]


class MemorySearchRequest(Schema):
    project_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    limit: int = Field(default=8, ge=1, le=20)


class MemorySearchHit(Schema):
    memory_id: str
    memory: str
    score: float | None = None
    source_event_id: str | None = None
    created_at: datetime | None = None


class MemorySearchResult(Schema):
    memories: list[MemorySearchHit]


class MemoryCorrectRequest(Schema):
    project_id: str = Field(min_length=1)
    memory_id: str = Field(min_length=1)
    correction: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    action: Literal["update", "delete"] = "update"


class MemoryCorrectResult(Schema):
    memory_id: str
    action: Literal["update", "delete"]
    replacement_memories: list[MemoryMutation] = Field(default_factory=list)


# --- Memory Service orchestration layer (P3-A) -------------------------------


class ErrorInfo(Schema):
    """Serializable form of a domain error, safe to return to callers."""

    code: str
    message: str
    details: dict[str, JsonValue] = Field(default_factory=dict)


class BootstrapRequest(Schema):
    project_id: str = Field(min_length=1)
    state_keys: list[str] | None = None
    decision_limit: int = Field(default=10, ge=1, le=100)
    task_limit: int = Field(default=20, ge=1, le=100)
    work_limit: int = Field(default=20, ge=1, le=100)


class BootstrapResult(Schema):
    project: Project
    state: StateSnapshot
    recent_decisions: list[Decision]
    open_tasks: list[Task]
    recent_work: list[WorkLog]


class StartTurnRequest(Schema):
    project_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    role: Literal["user", "assistant"] = "user"
    session_id: str | None = None
    turn_id: str | None = None
    agent_id: str | None = None


class StartTurnResult(Schema):
    event: RawEvent
    memory_status: Literal["done", "pending"]
    memories: list[MemoryMutation] = Field(default_factory=list)
    error: ErrorInfo | None = None


class SearchRequest(Schema):
    project_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    limit: int = Field(default=8, ge=1, le=20)


class SearchResult(Schema):
    status: Literal["ok", "degraded"]
    memories: list[MemorySearchHit] = Field(default_factory=list)
    error: ErrorInfo | None = None


class GetStateRequest(Schema):
    project_id: str = Field(min_length=1)
    keys: list[str] | None = None


class FinishTurnRequest(Schema):
    project_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    changed_files: list[str] = Field(default_factory=list)
    session_id: str | None = None
    agent_id: str | None = None
    commit_hash: str | None = None
    state_updates: dict[str, JsonValue] = Field(default_factory=dict)
    expected_state_version: int | None = Field(default=None, ge=0)
    source_event_id: str | None = None

    @model_validator(mode="after")
    def _require_version_for_updates(self) -> FinishTurnRequest:
        if self.state_updates and self.expected_state_version is None:
            raise ValueError("expected_state_version is required when state_updates is not empty")
        return self


class FinishTurnResult(Schema):
    work_log: WorkLog
    state: StateSnapshot
    state_updated: bool


class CorrectMemoryRequest(Schema):
    project_id: str = Field(min_length=1)
    memory_id: str = Field(min_length=1)
    correction: str = Field(min_length=1)
    action: Literal["update", "delete"] = "update"
    session_id: str | None = None
    agent_id: str | None = None
    state_updates: dict[str, JsonValue] = Field(default_factory=dict)
    expected_state_version: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _require_version_for_updates(self) -> CorrectMemoryRequest:
        if self.state_updates and self.expected_state_version is None:
            raise ValueError("expected_state_version is required when state_updates is not empty")
        return self


class CorrectMemoryResult(Schema):
    event: RawEvent
    work_log: WorkLog
    state: StateSnapshot
    state_updated: bool
    status: Literal["done", "pending"]
    mutation: MemoryCorrectResult | None = None
    error: ErrorInfo | None = None
