"""Stable domain errors returned by the Project Store layer."""

from __future__ import annotations

from typing import Any


class ProjectStoreError(RuntimeError):
    """Base error carrying a stable machine-readable code."""

    code = "PROJECT_STORE_ERROR"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "details": self.details}


class ProjectNotFoundError(ProjectStoreError):
    code = "PROJECT_NOT_FOUND"

    def __init__(self, project_id: str) -> None:
        super().__init__(f"Project does not exist: {project_id}", project_id=project_id)


class ProjectAlreadyExistsError(ProjectStoreError):
    code = "PROJECT_ALREADY_EXISTS"

    def __init__(self, project_id: str) -> None:
        super().__init__(f"Project already exists: {project_id}", project_id=project_id)


class RecordNotFoundError(ProjectStoreError):
    code = "RECORD_NOT_FOUND"

    def __init__(self, record_type: str, record_id: str) -> None:
        super().__init__(
            f"{record_type} does not exist: {record_id}",
            record_type=record_type,
            record_id=record_id,
        )


class StateConflictError(ProjectStoreError):
    code = "STATE_CONFLICT"

    def __init__(self, project_id: str, expected_version: int, actual_version: int) -> None:
        super().__init__(
            f"State version conflict for {project_id}: expected {expected_version}, "
            f"actual {actual_version}",
            project_id=project_id,
            expected_version=expected_version,
            actual_version=actual_version,
        )


class MemoryEngineError(RuntimeError):
    """Mem0 adapter failure with a stable machine-readable code."""

    code = "MEM0_UNAVAILABLE"

    def __init__(self, operation: str, cause: Exception) -> None:
        super().__init__(f"Mem0 {operation} failed: {cause}")
        self.operation = operation
        self.cause = cause

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "details": {"operation": self.operation, "cause_type": type(self.cause).__name__},
        }


class MemoryNotFoundError(MemoryEngineError):
    """A requested Mem0 record does not exist."""

    code = "MEMORY_NOT_FOUND"

    def __init__(self, memory_id: str) -> None:
        RuntimeError.__init__(self, f"Memory does not exist: {memory_id}")
        self.operation = "correct"
        self.cause = LookupError(memory_id)
        self.memory_id = memory_id

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "details": {"memory_id": self.memory_id},
        }


class MemoryProjectMismatchError(MemoryEngineError):
    """Prevent a caller from correcting another project's memory by ID."""

    code = "MEMORY_PROJECT_MISMATCH"

    def __init__(self, memory_id: str, project_id: str) -> None:
        RuntimeError.__init__(self, "Memory does not belong to the requested project")
        self.operation = "correct"
        self.cause = PermissionError(memory_id)
        self.memory_id = memory_id
        self.project_id = project_id

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "details": {"memory_id": self.memory_id, "project_id": self.project_id},
        }


class EmbeddingDimensionMismatchError(RuntimeError):
    """Persistent Qdrant collection is incompatible with embedder settings."""

    code = "EMBEDDING_DIMENSION_MISMATCH"

    def __init__(self, collection_name: str, expected: int, actual: int) -> None:
        super().__init__(
            f"Qdrant collection {collection_name!r} uses {actual} dimensions, "
            f"but EMBED_DIMS is {expected}"
        )
        self.collection_name = collection_name
        self.expected = expected
        self.actual = actual

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "details": {
                "collection_name": self.collection_name,
                "expected_dimensions": self.expected,
                "actual_dimensions": self.actual,
            },
        }
