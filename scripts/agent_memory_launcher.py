#!/usr/bin/env python3
"""Lightweight gate used by Codex, WorkBuddy, and Claude MCP configs."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(
    os.environ.get("AGENTNEXUS_PROJECT_ROOT", str(Path.home() / "Src/AgentNexus"))
)
SUPPORT_DIR = Path(
    os.environ.get(
        "AGENTNEXUS_SUPPORT_DIR",
        str(Path.home() / "Library/Application Support/AgentNexus Memory"),
    )
)
MODE_FILE = SUPPORT_DIR / "mode.json"
UV_BIN = Path(os.environ.get("AGENTNEXUS_UV_BIN", "/opt/homebrew/bin/uv"))


def read_mode() -> str:
    try:
        value = json.loads(MODE_FILE.read_text(encoding="utf-8")).get("mode")
    except (OSError, ValueError, TypeError):
        return "state-only"
    return value if value in {"off", "state-only", "full"} else "state-only"


def main() -> int:
    mode = read_mode()
    if mode == "off":
        print(
            "AgentNexus Memory is off. Open AgentNexus Memory.app to enable it.",
            file=sys.stderr,
        )
        return 75
    if not UV_BIN.is_file():
        print(f"AgentNexus Memory cannot find uv: {UV_BIN}", file=sys.stderr)
        return 69
    if not PROJECT_ROOT.is_dir():
        print(f"AgentNexus project is missing: {PROJECT_ROOT}", file=sys.stderr)
        return 69

    environment = os.environ.copy()
    environment["AGENT_MEMORY_MODE"] = mode
    environment.setdefault("MEM0_TELEMETRY", "false")
    arguments = [
        str(UV_BIN),
        "--directory",
        str(PROJECT_ROOT),
        "run",
        "python",
        "-m",
        "agent_memory_hub.mcp_server",
    ]
    os.execve(str(UV_BIN), arguments, environment)
    return 70


if __name__ == "__main__":
    raise SystemExit(main())
