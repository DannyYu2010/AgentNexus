from pathlib import Path
from tempfile import TemporaryDirectory

from agent_memory_hub import ProjectStore
from agent_memory_hub.errors import StateConflictError
from agent_memory_hub.schemas import DecisionCreate, ProjectCreate, StateUpdate


def main() -> None:
    with TemporaryDirectory() as directory:
        store = ProjectStore(Path(directory) / "project_state.db")
        store.initialize()
        store.create_project(ProjectCreate(id="memory-demo", name="Memory Demo"))

        first = store.update_state(
            StateUpdate(
                project_id="memory-demo",
                updates={"agw.reconnect": True, "agw.reconnect_interval_sec": 5},
                expected_version=0,
            )
        )
        old = store.add_decision(
            DecisionCreate(
                project_id="memory-demo",
                topic="agw.reconnect_interval_sec",
                decision="固定 5 秒重连",
            )
        )
        latest = store.add_decision(
            DecisionCreate(
                project_id="memory-demo",
                topic="agw.reconnect_interval_sec",
                decision="固定 10 秒重连",
            )
        )
        second = store.update_state(
            StateUpdate(
                project_id="memory-demo",
                updates={"agw.reconnect_interval_sec": 10},
                expected_version=first.state_version,
            )
        )

        try:
            store.update_state(
                StateUpdate(
                    project_id="memory-demo",
                    updates={"agw.reconnect_interval_sec": 15},
                    expected_version=first.state_version,
                )
            )
        except StateConflictError:
            pass
        else:
            raise AssertionError("stale state update was not rejected")

        decisions = store.list_recent_decisions("memory-demo")
        by_id = {decision.id: decision for decision in decisions}
        assert by_id[old.id].status == "superseded"
        assert by_id[old.id].superseded_by == latest.id
        assert second.items["agw.reconnect_interval_sec"].value == 10
        print("P0 smoke test passed")


if __name__ == "__main__":
    main()
