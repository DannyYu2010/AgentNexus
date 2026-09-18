from pathlib import Path

from agent_memory_hub import ProjectStore

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "project_state.db"


def main() -> None:
    ProjectStore(DB_PATH).initialize()
    print(DB_PATH)


if __name__ == "__main__":
    main()
