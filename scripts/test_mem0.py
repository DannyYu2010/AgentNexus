"""Live P1 smoke test against local Qwen, Hugging Face embeddings, and Qdrant."""

from __future__ import annotations

import sys

import httpx

from agent_memory_hub.config import MemorySettings
from agent_memory_hub.memory_engine import create_memory_engine
from agent_memory_hub.schemas import MemoryAddRequest, MemorySearchRequest


def detect_model(settings: MemorySettings) -> MemorySettings:
    models_url = f"{settings.qwen_base_url.rstrip('/')}/models"
    try:
        with httpx.Client(trust_env=False, timeout=10) as client:
            response = client.get(models_url)
            response.raise_for_status()
    except Exception as exc:
        raise RuntimeError(
            f"无法连接本地千问：{models_url}。请先启动 mlx_lm.server。原始错误：{exc}"
        ) from exc

    models = response.json().get("data", [])
    model_ids = [item.get("id") for item in models if item.get("id")]
    if not model_ids:
        raise RuntimeError(f"千问接口没有返回模型 ID：{response.text[:500]}")
    if settings.qwen_model in model_ids:
        return settings
    return settings.model_copy(update={"qwen_model": model_ids[0]})


def main() -> int:
    try:
        settings = detect_model(MemorySettings())
        print(f"Qwen model: {settings.qwen_model}")
        engine = create_memory_engine(settings)
        added = engine.add(
            MemoryAddRequest(
                project_id="mem0-smoke",
                event_id="evt-p1-smoke",
                session_id="p1-smoke",
                agent_id="codex-p1",
                content="AGW 需要自动重连，固定 5 秒一次，暂时不用指数退避。",
            )
        )
        print("ADD:", added.model_dump(mode="json"))

        found = engine.search(
            MemorySearchRequest(
                project_id="mem0-smoke",
                query="AGW 断线后应该怎么重连？",
                limit=8,
            )
        )
        print("SEARCH:", found.model_dump(mode="json"))
        combined = " ".join(item.memory for item in found.memories)
        if "5" not in combined or "重连" not in combined:
            raise AssertionError("没有召回预期的‘固定 5 秒重连’记忆")
    except Exception as exc:  # noqa: BLE001 - CLI boundary reports provider failures cleanly.
        print(f"P1 smoke test failed: {exc}", file=sys.stderr)
        return 1

    print("P1 Mem0 add/search smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
