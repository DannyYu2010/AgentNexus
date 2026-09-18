"""P2 live test: reopen Qdrant in a new process and verify project isolation."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import httpx

from agent_memory_hub.config import MemorySettings
from agent_memory_hub.memory_engine import create_memory_engine
from agent_memory_hub.schemas import MemoryAddRequest, MemorySearchRequest

COLLECTION = "p2_persistence_smoke"
PROJECT_A = "p2-project-a"
PROJECT_B = "p2-project-b"
EVENT_ID = "evt-p2-persistence"
MEMORY_TEXT = "P2 持久化验收：AGW 断线后固定每 7 秒重连一次。"
QUERY = "P2 验收时 AGW 断线后隔几秒重连？"


def settings_for(data_dir: Path) -> MemorySettings:
    return MemorySettings(memory_data_dir=data_dir, collection_name=COLLECTION)


def detect_model(settings: MemorySettings) -> MemorySettings:
    """Use the model ID actually served by the local OpenAI-compatible API."""
    models_url = f"{settings.qwen_base_url.rstrip('/')}/models"
    try:
        with httpx.Client(trust_env=False, timeout=10) as client:
            response = client.get(models_url)
            response.raise_for_status()
    except Exception as exc:
        raise RuntimeError(
            f"无法连接本地千问：{models_url}。请先启动 mlx_lm.server。原始错误：{exc}"
        ) from exc

    model_ids = [item.get("id") for item in response.json().get("data", []) if item.get("id")]
    if not model_ids:
        raise RuntimeError(f"千问接口没有返回模型 ID：{response.text[:500]}")
    if settings.qwen_model in model_ids:
        return settings
    return settings.model_copy(update={"qwen_model": model_ids[0]})


def assert_expected_memory(memories: list, context: str) -> None:
    combined = " ".join(item.memory for item in memories)
    if "7" not in combined or "重连" not in combined:
        raise AssertionError(f"{context}没有召回预期的‘固定 7 秒重连’记忆：{combined!r}")


def write_phase(data_dir: Path) -> None:
    settings = detect_model(settings_for(data_dir))
    print(f"WRITE model: {settings.qwen_model}")
    engine = create_memory_engine(settings)
    added = engine.add(
        MemoryAddRequest(
            project_id=PROJECT_A,
            event_id=EVENT_ID,
            session_id="p2-write-process",
            agent_id="codex-p2",
            content=MEMORY_TEXT,
        )
    )
    print("WRITE add:", added.model_dump(mode="json"))
    found = engine.search(
        MemorySearchRequest(project_id=PROJECT_A, query=QUERY, limit=8)
    )
    assert_expected_memory(found.memories, "写入进程中")
    print("WRITE verified before process exit")


def read_phase(data_dir: Path) -> None:
    engine = create_memory_engine(settings_for(data_dir))
    found = engine.search(
        MemorySearchRequest(project_id=PROJECT_A, query=QUERY, limit=8)
    )
    assert_expected_memory(found.memories, "新进程重开后")
    print("READ reopened result:", found.model_dump(mode="json"))

    isolated = engine.search(
        MemorySearchRequest(project_id=PROJECT_B, query=QUERY, limit=8)
    )
    if isolated.memories:
        raise AssertionError(
            f"项目隔离失败：{PROJECT_B} 看到了 {PROJECT_A} 的记忆："
            f"{isolated.model_dump(mode='json')}"
        )
    print(f"ISOLATION verified: {PROJECT_B} returned no memories")


def run_child(phase: str, data_dir: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--phase", phase, "--data-dir", str(data_dir)],
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"P2 {phase} 子进程失败，退出码：{result.returncode}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("write", "read"))
    parser.add_argument("--data-dir", type=Path, default=Path("data/p2-smoke"))
    args = parser.parse_args()
    data_dir = args.data_dir.resolve()

    try:
        if args.phase == "write":
            write_phase(data_dir)
        elif args.phase == "read":
            read_phase(data_dir)
        else:
            print(f"P2 data directory: {data_dir}")
            run_child("write", data_dir)
            print("WRITE process exited; starting a fresh READ process")
            run_child("read", data_dir)
            print("P2 persistence and project-isolation smoke test passed")
    except Exception as exc:  # noqa: BLE001 - CLI boundary reports provider failures cleanly.
        print(f"P2 persistence test failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
