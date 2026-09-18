"""Thin, testable adapter around Mem0 OSS."""

from __future__ import annotations

import os
from typing import Any, Protocol

from .config import MemorySettings
from .errors import (
    EmbeddingDimensionMismatchError,
    MemoryEngineError,
    MemoryNotFoundError,
    MemoryProjectMismatchError,
)
from .schemas import (
    MemoryAddRequest,
    MemoryAddResult,
    MemoryCorrectRequest,
    MemoryCorrectResult,
    MemoryMutation,
    MemorySearchHit,
    MemorySearchRequest,
    MemorySearchResult,
)


class Mem0Backend(Protocol):
    def add(self, messages: list[dict[str, str]], **kwargs: Any) -> dict: ...

    def search(self, query: str, **kwargs: Any) -> dict: ...

    def get(self, memory_id: str) -> dict: ...

    def update(self, memory_id: str, **kwargs: Any) -> dict: ...

    def delete(self, memory_id: str) -> None: ...


class MemoryEngine:
    """Project-scoped long-term memory operations."""

    def __init__(self, backend: Mem0Backend, user_id: str = "local-user") -> None:
        self.backend = backend
        self.user_id = user_id

    def add(self, request: MemoryAddRequest) -> MemoryAddResult:
        metadata = {
            "project_id": request.project_id,
            "event_id": request.event_id,
            "session_id": request.session_id,
            # Mem0 2.x reserves `agent_id` as an identity filter and rejects it
            # inside metadata. Keep the source attribution under our own key.
            "source_agent_id": request.agent_id,
            "source_role": request.source_role,
        }
        metadata = {key: value for key, value in metadata.items() if value is not None}
        try:
            response = self.backend.add(
                [{"role": request.source_role, "content": request.content}],
                user_id=self.user_id,
                metadata=metadata,
                infer=True,
            )
        except Exception as exc:
            raise MemoryEngineError("add", exc) from exc

        memories = []
        for item in response.get("results", []):
            memory_id = item.get("id")
            memory = item.get("memory")
            if memory_id and memory:
                memories.append(
                    MemoryMutation(
                        memory_id=str(memory_id),
                        memory=str(memory),
                        event=str(item.get("event", "UNKNOWN")),
                    )
                )
        return MemoryAddResult(memories=memories)

    def search(self, request: MemorySearchRequest) -> MemorySearchResult:
        try:
            response = self.backend.search(
                request.query,
                top_k=request.limit,
                filters={"user_id": self.user_id, "project_id": request.project_id},
                rerank=False,
            )
        except Exception as exc:
            raise MemoryEngineError("search", exc) from exc

        memories = []
        for item in response.get("results", []):
            memory_id = item.get("id")
            memory = item.get("memory")
            if not memory_id or not memory:
                continue
            metadata = item.get("metadata") or {}
            memories.append(
                MemorySearchHit(
                    memory_id=str(memory_id),
                    memory=str(memory),
                    score=item.get("score"),
                    source_event_id=metadata.get("event_id") or item.get("event_id"),
                    created_at=item.get("created_at"),
                )
            )
        return MemorySearchResult(memories=memories)

    def correct(self, request: MemoryCorrectRequest) -> MemoryCorrectResult:
        """Correct one project-scoped memory while preserving Mem0 history."""
        try:
            existing = self.backend.get(request.memory_id)
        except Exception as exc:
            raise MemoryEngineError("get_for_correct", exc) from exc
        if not existing:
            raise MemoryNotFoundError(request.memory_id)
        metadata = existing.get("metadata") or {}
        if metadata.get("project_id") != request.project_id:
            raise MemoryProjectMismatchError(request.memory_id, request.project_id)

        try:
            if request.action == "update":
                self.backend.update(request.memory_id, text=request.correction)
                return MemoryCorrectResult(memory_id=request.memory_id, action="update")

            self.backend.delete(request.memory_id)
            response = self.backend.add(
                [{"role": "user", "content": request.correction}],
                user_id=self.user_id,
                metadata={
                    "project_id": request.project_id,
                    "event_id": request.event_id,
                    "source_role": "user",
                    "correction_of": request.memory_id,
                },
                infer=False,
            )
        except Exception as exc:
            raise MemoryEngineError("correct", exc) from exc

        replacements = []
        for item in response.get("results", []):
            memory_id = item.get("id")
            memory = item.get("memory")
            if memory_id and memory:
                replacements.append(
                    MemoryMutation(
                        memory_id=str(memory_id),
                        memory=str(memory),
                        event=str(item.get("event", "ADD")),
                    )
                )
        return MemoryCorrectResult(
            memory_id=request.memory_id,
            action="delete",
            replacement_memories=replacements,
        )


def create_memory_engine(settings: MemorySettings | None = None) -> MemoryEngine:
    """Lazily create the heavyweight Mem0/Hugging Face stack."""
    settings = settings or MemorySettings()
    settings.ensure_data_directories()
    _ensure_loopback_bypasses_proxy()
    validate_qdrant_dimensions(settings)

    # Mem0 telemetry is opt-out. This local project keeps it disabled unless the
    # user explicitly opts in before the module is imported.
    os.environ.setdefault("MEM0_TELEMETRY", "false")
    from mem0 import Memory

    backend = Memory.from_config(settings.to_mem0_config())
    return MemoryEngine(backend, user_id=settings.mem0_user_id)


def validate_qdrant_dimensions(settings: MemorySettings) -> None:
    """Reject an existing collection that cannot hold current embeddings.

    Mem0/Qdrant otherwise reports this incompatibility only during a later
    operation. Checking at startup produces a stable, actionable error.
    """
    client = _qdrant_client(settings)
    try:
        collections = {item.name for item in client.get_collections().collections}
        if settings.collection_name not in collections:
            return
        vectors = client.get_collection(settings.collection_name).config.params.vectors
        actual = _vector_size(vectors)
        if actual is not None and actual != settings.embed_dims:
            raise EmbeddingDimensionMismatchError(
                settings.collection_name,
                expected=settings.embed_dims,
                actual=actual,
            )
    finally:
        client.close()


def _qdrant_client(settings: MemorySettings) -> Any:
    from qdrant_client import QdrantClient

    if settings.qdrant_mode == "server":
        return QdrantClient(
            host=settings.qdrant_host,
            port=settings.qdrant_port,
            api_key=settings.qdrant_api_key,
        )
    return QdrantClient(path=str(settings.qdrant_path))


def _vector_size(vectors: Any) -> int | None:
    """Read the size of Mem0's unnamed vector, tolerating Qdrant model variants."""
    if hasattr(vectors, "size"):
        return int(vectors.size)
    if isinstance(vectors, dict) and len(vectors) == 1:
        vector = next(iter(vectors.values()))
        if hasattr(vector, "size"):
            return int(vector.size)
    return None


def _ensure_loopback_bypasses_proxy() -> None:
    """Keep local Qwen traffic away from macOS/system HTTP proxies."""
    required = {"127.0.0.1", "localhost"}
    for variable in ("NO_PROXY", "no_proxy"):
        existing = {item.strip() for item in os.environ.get(variable, "").split(",") if item}
        os.environ[variable] = ",".join(sorted(existing | required))
