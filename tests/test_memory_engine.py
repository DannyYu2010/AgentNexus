from __future__ import annotations

from pathlib import Path

import pytest
from qdrant_client import QdrantClient, models

from agent_memory_hub.config import MemorySettings
from agent_memory_hub.errors import (
    EmbeddingDimensionMismatchError,
    MemoryEngineError,
    MemoryProjectMismatchError,
)
from agent_memory_hub.memory_engine import MemoryEngine, validate_qdrant_dimensions
from agent_memory_hub.schemas import (
    MemoryAddRequest,
    MemoryCorrectRequest,
    MemorySearchRequest,
)


class FakeMem0:
    def __init__(self) -> None:
        self.add_call = None
        self.search_call = None
        self.update_call = None
        self.delete_call = None
        self.memories = {
            "mem-1": {
                "id": "mem-1",
                "memory": "AGW 固定 5 秒重连",
                "metadata": {"project_id": "demo", "event_id": "evt-1"},
            }
        }

    def add(self, messages, **kwargs):
        self.add_call = (messages, kwargs)
        return {
            "results": [
                {"id": "mem-1", "memory": "AGW 固定 5 秒重连", "event": "ADD"}
            ]
        }

    def search(self, query, **kwargs):
        self.search_call = (query, kwargs)
        return {
            "results": [
                {
                    "id": "mem-1",
                    "memory": "AGW 固定 5 秒重连",
                    "score": 0.92,
                    "metadata": {"event_id": "evt-1", "project_id": "demo"},
                    "created_at": "2026-09-01T10:00:00+00:00",
                }
            ]
        }

    def get(self, memory_id):
        return self.memories.get(memory_id)

    def update(self, memory_id, **kwargs):
        self.update_call = (memory_id, kwargs)
        self.memories[memory_id]["memory"] = kwargs["text"]
        return {"message": "Memory updated successfully!"}

    def delete(self, memory_id):
        self.delete_call = memory_id
        self.memories.pop(memory_id)


def test_settings_build_persistent_local_mem0_config(tmp_path: Path) -> None:
    settings = MemorySettings(
        memory_data_dir=tmp_path,
        qwen_model="qwen-local",
        qdrant_mode="local",
    )
    config = settings.to_mem0_config()

    assert config["llm"]["config"]["model"] == "qwen-local"
    assert config["llm"]["config"]["openai_base_url"].endswith("/v1")
    assert config["embedder"]["config"]["embedding_dims"] == 1024
    assert config["vector_store"]["config"]["path"] == str(tmp_path / "qdrant")
    assert config["vector_store"]["config"]["on_disk"] is True
    assert config["history_db_path"] == str(tmp_path / "mem0_history.db")


def test_settings_build_qdrant_server_mem0_config(tmp_path: Path) -> None:
    settings = MemorySettings(
        memory_data_dir=tmp_path,
        qdrant_mode="server",
        qdrant_host="127.0.0.1",
        qdrant_port=6333,
    )

    vector = settings.to_mem0_config()["vector_store"]["config"]

    assert vector["host"] == "127.0.0.1"
    assert vector["port"] == 6333
    assert "path" not in vector
    assert not settings.qdrant_path.exists()


def test_memory_engine_adds_project_metadata_and_normalizes_result() -> None:
    backend = FakeMem0()
    engine = MemoryEngine(backend)
    result = engine.add(
        MemoryAddRequest(
            project_id="demo",
            content="固定 5 秒重连",
            event_id="evt-1",
            session_id="session-1",
            agent_id="codex-a",
        )
    )

    assert result.memories[0].memory_id == "mem-1"
    messages, kwargs = backend.add_call
    assert messages == [{"role": "user", "content": "固定 5 秒重连"}]
    assert kwargs["user_id"] == "local-user"
    assert kwargs["metadata"]["project_id"] == "demo"
    assert kwargs["metadata"]["event_id"] == "evt-1"
    assert kwargs["metadata"]["source_agent_id"] == "codex-a"
    assert kwargs["infer"] is True


def test_memory_engine_search_is_project_scoped() -> None:
    backend = FakeMem0()
    engine = MemoryEngine(backend)
    result = engine.search(
        MemorySearchRequest(project_id="demo", query="怎么重连？", limit=5)
    )

    assert result.memories[0].source_event_id == "evt-1"
    query, kwargs = backend.search_call
    assert query == "怎么重连？"
    assert kwargs["top_k"] == 5
    assert kwargs["filters"] == {"user_id": "local-user", "project_id": "demo"}


def test_memory_engine_wraps_backend_failures() -> None:
    class BrokenMem0(FakeMem0):
        def add(self, messages, **kwargs):
            raise ConnectionError("Qwen offline")

    with pytest.raises(MemoryEngineError) as failure:
        MemoryEngine(BrokenMem0()).add(
            MemoryAddRequest(project_id="demo", content="hello", event_id="evt-1")
        )
    assert failure.value.code == "MEM0_UNAVAILABLE"
    assert failure.value.operation == "add"


def test_memory_engine_updates_only_memory_in_requested_project() -> None:
    backend = FakeMem0()

    result = MemoryEngine(backend).correct(
        MemoryCorrectRequest(
            project_id="demo",
            memory_id="mem-1",
            correction="AGW 固定 10 秒重连",
            event_id="evt-correct",
        )
    )

    assert result.action == "update"
    assert backend.update_call == ("mem-1", {"text": "AGW 固定 10 秒重连"})


def test_memory_engine_rejects_cross_project_correction() -> None:
    backend = FakeMem0()

    with pytest.raises(MemoryProjectMismatchError):
        MemoryEngine(backend).correct(
            MemoryCorrectRequest(
                project_id="other",
                memory_id="mem-1",
                correction="不得修改",
                event_id="evt-correct",
            )
        )

    assert backend.update_call is None


def test_memory_engine_delete_writes_explicit_replacement() -> None:
    backend = FakeMem0()

    result = MemoryEngine(backend).correct(
        MemoryCorrectRequest(
            project_id="demo",
            memory_id="mem-1",
            correction="正确事实是固定 10 秒重连",
            event_id="evt-correct",
            action="delete",
        )
    )

    assert backend.delete_call == "mem-1"
    messages, kwargs = backend.add_call
    assert messages[0]["content"] == "正确事实是固定 10 秒重连"
    assert kwargs["infer"] is False
    assert kwargs["metadata"]["correction_of"] == "mem-1"
    assert result.action == "delete"


def test_qdrant_dimension_preflight_accepts_matching_collection(tmp_path: Path) -> None:
    settings = MemorySettings(
        memory_data_dir=tmp_path,
        collection_name="dimension-test",
        embed_dims=4,
        qdrant_mode="local",
    )
    settings.ensure_data_directories()
    client = QdrantClient(path=str(settings.qdrant_path))
    client.create_collection(
        settings.collection_name,
        vectors_config=models.VectorParams(size=4, distance=models.Distance.COSINE),
    )
    client.close()

    validate_qdrant_dimensions(settings)


def test_qdrant_dimension_preflight_returns_stable_error(tmp_path: Path) -> None:
    settings = MemorySettings(
        memory_data_dir=tmp_path,
        collection_name="dimension-test",
        embed_dims=8,
        qdrant_mode="local",
    )
    settings.ensure_data_directories()
    client = QdrantClient(path=str(settings.qdrant_path))
    client.create_collection(
        settings.collection_name,
        vectors_config=models.VectorParams(size=4, distance=models.Distance.COSINE),
    )
    client.close()

    with pytest.raises(EmbeddingDimensionMismatchError) as failure:
        validate_qdrant_dimensions(settings)

    assert failure.value.as_dict() == {
        "code": "EMBEDDING_DIMENSION_MISMATCH",
        "message": (
            "Qdrant collection 'dimension-test' uses 4 dimensions, but EMBED_DIMS is 8"
        ),
        "details": {
            "collection_name": "dimension-test",
            "expected_dimensions": 8,
            "actual_dimensions": 4,
        },
    }
