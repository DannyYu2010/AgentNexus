"""Configuration for the local-only Mem0 adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

CUSTOM_MEMORY_INSTRUCTIONS = """
只提取用户或 Agent 明确表达、对未来项目协作有价值的信息。
重点保留：需求、约束、决策、问题、结果、偏好和可验证的工程事实。
不要把推测写成事实，不要记录寒暄或一次性的无关内容。
保留变化历史；旧决定可以作为历史，但不要描述成当前权威状态。
""".strip()


class MemorySettings(BaseSettings):
    """Environment-backed settings with safe local defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    memory_data_dir: Path = Field(default=Path("./data"), validation_alias="MEMORY_DATA_DIR")
    qwen_base_url: str = Field(
        default="http://127.0.0.1:8080/v1", validation_alias="QWEN_BASE_URL"
    )
    qwen_api_key: str = Field(default="local", validation_alias="QWEN_API_KEY")
    qwen_model: str = Field(
        default="mlx-community/Qwen3.8-27B-4bit", validation_alias="QWEN_MODEL"
    )
    embed_model: str = Field(
        default="Qwen/Qwen3-Embedding-0.6B", validation_alias="EMBED_MODEL"
    )
    embed_dims: int = Field(default=1024, validation_alias="EMBED_DIMS", gt=0)
    mem0_user_id: str = Field(default="local-user", validation_alias="MEM0_USER_ID")
    collection_name: str = Field(
        default="agent_memories", validation_alias="MEM0_COLLECTION_NAME"
    )
    qdrant_mode: Literal["local", "server"] = Field(
        default="local", validation_alias="QDRANT_MODE"
    )
    qdrant_host: str = Field(default="127.0.0.1", validation_alias="QDRANT_HOST")
    qdrant_port: int = Field(default=6333, validation_alias="QDRANT_PORT", ge=1, le=65535)
    qdrant_api_key: str | None = Field(default=None, validation_alias="QDRANT_API_KEY")
    memory_mode: Literal["full", "state-only"] = Field(
        default="full", validation_alias="AGENT_MEMORY_MODE"
    )

    def ensure_data_directories(self) -> None:
        self.memory_data_dir.mkdir(parents=True, exist_ok=True)
        if self.qdrant_mode == "local":
            self.qdrant_path.mkdir(parents=True, exist_ok=True)

    @property
    def qdrant_path(self) -> Path:
        return self.memory_data_dir / "qdrant"

    @property
    def history_db_path(self) -> Path:
        return self.memory_data_dir / "mem0_history.db"

    def to_mem0_config(self) -> dict:
        """Build the exact config accepted by mem0ai 2.x."""
        vector_config: dict = {
            "collection_name": self.collection_name,
            "embedding_model_dims": self.embed_dims,
            "on_disk": True,
        }
        if self.qdrant_mode == "server":
            vector_config.update(host=self.qdrant_host, port=self.qdrant_port)
            if self.qdrant_api_key:
                vector_config["api_key"] = self.qdrant_api_key
        else:
            vector_config["path"] = str(self.qdrant_path)

        return {
            "llm": {
                "provider": "openai",
                "config": {
                    "model": self.qwen_model,
                    "api_key": self.qwen_api_key,
                    "openai_base_url": self.qwen_base_url.rstrip("/"),
                    "temperature": 0.0,
                    "max_tokens": 1200,
                    "is_reasoning_model": False,
                },
            },
            "embedder": {
                "provider": "huggingface",
                "config": {
                    "model": self.embed_model,
                    "embedding_dims": self.embed_dims,
                },
            },
            "vector_store": {
                "provider": "qdrant",
                "config": vector_config,
            },
            "history_db_path": str(self.history_db_path),
            "custom_instructions": CUSTOM_MEMORY_INSTRUCTIONS,
            "version": "v1.1",
        }
