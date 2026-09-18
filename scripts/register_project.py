"""Register a project in the authoritative Project Store."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_memory_hub.config import MemorySettings
from agent_memory_hub.errors import ProjectNotFoundError
from agent_memory_hub.project_store import ProjectStore
from agent_memory_hub.schemas import ProjectCreate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_id", help="Stable project identifier used by MCP tools")
    parser.add_argument("name", help="Human-readable project name")
    parser.add_argument("--repo-path", type=Path, default=Path.cwd())
    parser.add_argument("--db-path", type=Path)
    args = parser.parse_args()

    settings = MemorySettings()
    db_path = args.db_path or settings.memory_data_dir / "project_state.db"
    store = ProjectStore(db_path)
    store.initialize()

    try:
        project = store.get_project(args.project_id)
        action = "already registered"
    except ProjectNotFoundError:
        project = store.create_project(
            ProjectCreate(
                id=args.project_id,
                name=args.name,
                repo_path=str(args.repo_path.resolve()),
            )
        )
        action = "registered"

    print(f"Project {action}: {project.id} ({project.name})")
    print(f"Database: {Path(db_path).resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
