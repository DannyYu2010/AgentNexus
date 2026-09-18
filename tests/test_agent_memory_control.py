from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/agent_memory_control.py"
LAUNCHER_SCRIPT = Path(__file__).parents[1] / "scripts/agent_memory_launcher.py"


def load_control(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    home = tmp_path / "home"
    support = tmp_path / "support"
    app = tmp_path / "AgentNexus Memory.app"
    monkeypatch.setenv("AGENTNEXUS_HOME", str(home))
    monkeypatch.setenv("AGENTNEXUS_SUPPORT_DIR", str(support))
    monkeypatch.setenv("AGENTNEXUS_APP_PATH", str(app))
    spec = importlib.util.spec_from_file_location(f"control_{id(tmp_path)}", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_launcher(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    monkeypatch.setenv("AGENTNEXUS_SUPPORT_DIR", str(tmp_path / "support"))
    spec = importlib.util.spec_from_file_location(f"launcher_{id(tmp_path)}", LAUNCHER_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_install_client_configs_updates_all_clients_and_creates_backup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    control = load_control(monkeypatch, tmp_path)
    launcher = control.APP_PATH / "Contents/Resources/agent-memory-launcher"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    paths = control.client_paths()
    paths["codex"].parent.mkdir(parents=True)
    paths["codex"].write_text(
        "[mcp_servers.agent-memory]\n"
        'command = "/opt/homebrew/bin/uv"\n'
        'args = ["old"]\n'
        "startup_timeout_sec = 20\n\n"
        "[mcp_servers.agent-memory.env]\n"
        'MEM0_TELEMETRY = "false"\n',
        encoding="utf-8",
    )
    for name in ("workbuddy", "codebuddy", "claude"):
        write_json(
            paths[name],
            {
                "mcpServers": {"agent-memory": {"command": "old", "args": ["old"]}},
                "disabledMcpServers": ["agent-memory", "other"],
            },
        )

    result = control.install_client_configs(launcher)

    assert Path(result["backup"]).joinpath("manifest.json").is_file()
    assert all(item["configured"] for item in result["clients"].values())
    codex = paths["codex"].read_text(encoding="utf-8")
    assert f'command = "{launcher}"' in codex
    assert "startup_timeout_sec = 20" in codex
    assert control.read_mode() == "full"
    codebuddy = json.loads(paths["codebuddy"].read_text(encoding="utf-8"))
    assert codebuddy["disabledMcpServers"] == ["other"]

    second = control.install_client_configs(launcher)

    assert second["backup"] is None


def test_stop_qwen_is_a_no_op_when_nothing_is_running(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    control = load_control(monkeypatch, tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(control, "run", lambda args, **kwargs: calls.append(args))
    monkeypatch.setattr(control, "launchd_loaded", lambda label: False)
    monkeypatch.setattr(control, "qwen_is_up", lambda: False)

    control.stop_qwen()

    assert calls == []


def test_stop_qwen_kills_a_manually_started_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A Qwen the app did not launch must still go down when the user says off."""
    control = load_control(monkeypatch, tmp_path)
    monkeypatch.setattr(control, "launchd_loaded", lambda label: False)
    alive = {"value": True}
    monkeypatch.setattr(control, "qwen_is_up", lambda: alive["value"])
    monkeypatch.setattr(
        control, "qwen_processes", lambda: [{"pid": 4242, "command": "mlx_lm.server"}]
    )
    killed: list[int] = []

    def fake_terminate(pids: set[int]) -> set[int]:
        killed.extend(sorted(pids))
        alive["value"] = False
        return set()

    monkeypatch.setattr(control, "terminate_pids", fake_terminate)
    monkeypatch.setattr(control, "launchd_job_for_pid", lambda pid: None)
    monkeypatch.setattr(control, "process_exists", lambda pid: True)
    monkeypatch.setattr(
        control,
        "wait_until",
        lambda predicate, expected, timeout: bool(predicate()) is expected,
    )

    assert control.stop_qwen()["pids"] == [4242]
    assert killed == [4242]


def test_qwen_processes_identifies_the_server_by_port_not_by_command_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`python -m mlx_lm server` and wrappers must be recognised, not just argv text."""
    control = load_control(monkeypatch, tmp_path)
    monkeypatch.setattr(control, "qwen_status", lambda: {"healthy": True})
    monkeypatch.setattr(
        control, "process_commands", lambda: {77: "/usr/bin/python3.12 -m serve_it"}
    )
    monkeypatch.setattr(control, "port_listener_pids", lambda port: [77])

    assert control.qwen_processes() == [
        {"pid": 77, "command": "/usr/bin/python3.12 -m serve_it"}
    ]


def test_qwen_processes_disowns_a_foreign_service_on_the_port(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    control = load_control(monkeypatch, tmp_path)
    monkeypatch.setattr(control, "qwen_status", lambda: {"healthy": False})
    monkeypatch.setattr(control, "process_commands", lambda: {88: "/opt/some-other-api"})
    monkeypatch.setattr(control, "port_listener_pids", lambda port: [88])

    assert control.qwen_processes() == []


def test_qwen_processes_falls_back_to_command_matching_without_lsof(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    control = load_control(monkeypatch, tmp_path)
    monkeypatch.setattr(control, "qwen_status", lambda: {"healthy": False})
    monkeypatch.setattr(
        control, "process_commands", lambda: {99: "python -m mlx_lm server --port 8080"}
    )
    monkeypatch.setattr(control, "port_listener_pids", lambda port: [])

    assert [item["pid"] for item in control.qwen_processes()] == [99]


def test_stop_qwen_reports_a_supervisor_that_restarts_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Coming back three seconds later is not a successful shutdown."""
    control = load_control(monkeypatch, tmp_path)
    monkeypatch.setattr(control, "launchd_loaded", lambda label: False)
    monkeypatch.setattr(
        control, "qwen_processes", lambda: [{"pid": 4242, "command": "mlx_lm.server"}]
    )
    monkeypatch.setattr(control, "terminate_pids", lambda pids: set())
    monkeypatch.setattr(control, "launchd_job_for_pid", lambda pid: None)
    monkeypatch.setattr(control, "process_exists", lambda pid: True)
    monkeypatch.setattr(control, "port_listener_pids", lambda port: [4343])
    monkeypatch.setattr(control, "process_commands", lambda: {4343: "mlx_lm.server", 1: "launchd"})
    monkeypatch.setattr(control, "process_parents", lambda: {4343: 1})
    # Liveness probes in order: still up, still up, down after the kill, back again.
    phases = iter([True, True, False, True])
    monkeypatch.setattr(control, "qwen_is_up", lambda: next(phases, True))

    def fake_wait(predicate, expected, timeout):
        return bool(predicate()) is expected

    monkeypatch.setattr(control, "wait_until", fake_wait)

    with pytest.raises(control.ControlError, match="something is restarting it"):
        control.stop_qwen()


def test_stop_qwen_unloads_the_launchd_job_instead_of_only_killing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Signalling a supervised process is futile: launchd just starts a new one."""
    control = load_control(monkeypatch, tmp_path)
    monkeypatch.setattr(control, "launchd_loaded", lambda label: False)
    monkeypatch.setattr(
        control, "qwen_processes", lambda: [{"pid": 30483, "command": "mlx_lm.server"}]
    )
    monkeypatch.setattr(control, "launchd_job_for_pid", lambda pid: "com.agentnexus.qwen3-8")
    monkeypatch.setattr(control, "process_exists", lambda pid: False)
    monkeypatch.setattr(
        control, "terminate_pids", lambda pids: pytest.fail("must unload before signalling")
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        control,
        "run",
        lambda args, **kwargs: calls.append(args)
        or type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )
    phases = iter([True, True, False, False])
    monkeypatch.setattr(control, "qwen_is_up", lambda: next(phases, False))
    monkeypatch.setattr(
        control,
        "wait_until",
        lambda predicate, expected, timeout: bool(predicate()) is expected,
    )

    report = control.stop_qwen()

    assert report["unloaded_jobs"] == ["com.agentnexus.qwen3-8"]
    assert ["/bin/launchctl", "remove", "com.agentnexus.qwen3-8"] in calls


def test_supervisor_unload_never_touches_apple_or_gui_application_jobs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    control = load_control(monkeypatch, tmp_path)
    labels = iter(["com.apple.betaenrollmentagent", "application.com.agentnexus.memory.1.2"])
    monkeypatch.setattr(control, "launchd_job_for_pid", lambda pid: next(labels))
    monkeypatch.setattr(
        control, "unload_launchd_job", lambda label: pytest.fail(f"must not unload {label}")
    )

    assert control.unload_qwen_supervisors([{"pid": 1}, {"pid": 2}]) == []


def test_launchd_job_for_pid_reads_the_pid_column(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    control = load_control(monkeypatch, tmp_path)
    listing = (
        "30483\t-15\tcom.agentnexus.qwen3-8\n"
        "-\t-9\tcom.apple.betaenrollmentagent\n"
        "30319\t0\tapplication.com.agentnexus.memory.1847629.1847634\n"
    )
    monkeypatch.setattr(
        control,
        "run",
        lambda args, **kwargs: type("Result", (), {"returncode": 0, "stdout": listing})(),
    )

    assert control.launchd_job_for_pid(30483) == "com.agentnexus.qwen3-8"
    assert control.launchd_job_for_pid(999) is None


def test_listener_description_names_the_parent_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    control = load_control(monkeypatch, tmp_path)
    monkeypatch.setattr(control, "port_listener_pids", lambda port: [4343])
    monkeypatch.setattr(
        control, "process_commands", lambda: {4343: "mlx_lm.server", 900: "/opt/supervisor"}
    )
    monkeypatch.setattr(control, "process_parents", lambda: {4343: 900})

    description = control.describe_qwen_listeners()

    assert "pid 4343 [mlx_lm.server]" in description
    assert "started by pid 900 [/opt/supervisor]" in description


def test_stop_qwen_leaves_an_unrecognised_port_owner_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    control = load_control(monkeypatch, tmp_path)
    monkeypatch.setattr(control, "launchd_loaded", lambda label: False)
    monkeypatch.setattr(control, "qwen_is_up", lambda: True)
    monkeypatch.setattr(control, "qwen_processes", list)
    monkeypatch.setattr(control, "port_listener_pids", lambda port: [321])
    monkeypatch.setattr(control, "process_commands", lambda: {321: "/opt/other"})
    monkeypatch.setattr(control, "process_parents", lambda: {321: 1})
    monkeypatch.setattr(
        control, "terminate_pids", lambda pids: pytest.fail("must not kill unknown owners")
    )
    monkeypatch.setattr(
        control,
        "wait_until",
        lambda predicate, expected, timeout: bool(predicate()) is expected,
    )

    with pytest.raises(control.ControlError, match=r"pid 321 \[/opt/other\]"):
        control.stop_qwen()


def test_owned_qwen_is_unloaded_through_launchd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    control = load_control(monkeypatch, tmp_path)
    control.atomic_write_json(
        control.QWEN_OWNER_FILE,
        {"label": control.QWEN_LABEL, "model": control.QWEN_MODEL},
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        control,
        "run",
        lambda args, **kwargs: calls.append(args)
        or type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )
    loaded = {"value": True}
    monkeypatch.setattr(control, "launchd_loaded", lambda label: loaded["value"])
    # Stubbed rather than derived from lsof: whether this machine has lsof at
    # all must not decide whether the test exercises the launchd path.
    monkeypatch.setattr(control, "qwen_is_up", lambda: False)

    def fake_wait(predicate, expected, timeout):
        loaded["value"] = False
        return bool(predicate()) is expected

    monkeypatch.setattr(control, "wait_until", fake_wait)

    control.stop_qwen()

    assert ["/bin/launchctl", "remove", control.QWEN_LABEL] in calls
    assert not control.QWEN_OWNER_FILE.exists()


def test_full_mode_records_the_mode_before_starting_dependencies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    control = load_control(monkeypatch, tmp_path)
    events: list[str] = []
    monkeypatch.setattr(control, "start_qdrant", lambda: events.append("qdrant"))
    monkeypatch.setattr(control, "start_qwen_if_needed", lambda: events.append("qwen"))
    monkeypatch.setattr(control, "write_mode", lambda mode: events.append(f"mode:{mode}"))
    monkeypatch.setattr(control, "status_payload", lambda: {"ok": True})

    result = control.set_mode("full")

    assert events == ["mode:full", "qdrant", "qwen"]
    assert result["previous_mode"] == "state-only"
    assert result["warnings"] == []


def test_full_mode_stays_applied_when_a_component_fails_to_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stuck Qwen must not leave the launcher gate closed for every client."""
    control = load_control(monkeypatch, tmp_path)
    monkeypatch.setattr(control, "start_qdrant", lambda: None)

    def failing_qwen() -> None:
        raise control.ControlError("Qwen did not become ready within 300 seconds")

    monkeypatch.setattr(control, "start_qwen_if_needed", failing_qwen)
    monkeypatch.setattr(control, "status_payload", lambda: {"ok": True})

    result = control.set_mode("full")

    assert control.read_mode() == "full"
    assert result["ok"] is False
    assert "Qwen did not become ready" in result["error"]


def test_start_qdrant_is_idempotent_when_health_check_is_already_green(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    control = load_control(monkeypatch, tmp_path)
    monkeypatch.setattr(control, "url_ready", lambda url: True)
    monkeypatch.setattr(
        control, "run", lambda *args, **kwargs: pytest.fail("must not restart Qdrant")
    )

    control.start_qdrant()


def test_mcp_readiness_is_true_without_a_live_stdio_connection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    control = load_control(monkeypatch, tmp_path)
    launcher = control.DEFAULT_LAUNCHER
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)
    clients = {name: {"configured": True} for name in control.client_paths()}

    result = control.mcp_readiness("full", clients, launcher)

    assert result == {"enabled": True, "ready": True, "reasons": []}


def test_reconcile_repairs_client_launcher_configuration_when_needed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    control = load_control(monkeypatch, tmp_path)
    launcher = control.DEFAULT_LAUNCHER
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)
    repaired: list[Path] = []
    monkeypatch.setattr(
        control,
        "boot_snapshot",
        lambda: {
            "mode": "full",
            "qwen_switch": "off",
            "qdrant_healthy": True,
            "qwen_healthy": False,
            "mcp_ready": False,
        },
    )
    monkeypatch.setattr(
        control,
        "install_client_configs",
        lambda path: repaired.append(path) or {"clients": {}, "backup": None},
    )
    monkeypatch.setattr(control, "status_payload", lambda: {"ok": True})
    monkeypatch.setattr(control, "url_ready", lambda url, timeout=1.5: True)
    monkeypatch.setattr(control, "qwen_status", lambda: {"healthy": False})

    result = control.reconcile()

    assert repaired == [launcher]
    assert result["reconciled"] == ["mcp-launcher"]


def test_state_only_blocks_new_launches_before_stopping_heavy_components(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    control = load_control(monkeypatch, tmp_path)
    events: list[str] = []
    monkeypatch.setattr(control, "write_mode", lambda mode: events.append(f"mode:{mode}"))
    monkeypatch.setattr(
        control, "terminate_mcp_processes", lambda: events.append("mcp") or [101]
    )
    monkeypatch.setattr(control, "stop_qdrant", lambda: events.append("qdrant"))
    monkeypatch.setattr(control, "stop_qwen", lambda: events.append("qwen"))
    monkeypatch.setattr(control, "status_payload", lambda: {"ok": True})

    result = control.set_mode("state-only")

    assert events == ["mode:state-only", "mcp", "qdrant", "qwen"]
    assert result["stopped_mcp_pids"] == [101]


def test_qwen_switch_defaults_to_what_the_mode_implies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    control = load_control(monkeypatch, tmp_path)

    control.write_mode("state-only")
    assert control.read_qwen_switch() == "off"

    control.write_mode("full")
    assert control.read_qwen_switch() == "on"


def test_set_qwen_on_starts_qwen_without_touching_mode_or_qdrant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The switch owns port 8080 and nothing else."""
    control = load_control(monkeypatch, tmp_path)
    control.write_mode("state-only")
    events: list[str] = []
    monkeypatch.setattr(control, "start_qwen_if_needed", lambda: events.append("start"))
    monkeypatch.setattr(control, "start_qdrant", lambda: pytest.fail("must not touch Qdrant"))
    monkeypatch.setattr(control, "stop_qdrant", lambda: pytest.fail("must not touch Qdrant"))
    monkeypatch.setattr(control, "status_payload", lambda: {"ok": True})

    result = control.set_qwen("on")

    assert events == ["start"]
    assert control.read_qwen_switch() == "on"
    assert control.read_mode() == "state-only"
    assert result["previous_qwen_switch"] == "off"


def test_set_qwen_off_is_allowed_in_full_mode_and_shows_up_as_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    control = load_control(monkeypatch, tmp_path)
    control.write_mode("full")
    monkeypatch.setattr(control, "stop_qwen", lambda: {"pids": [], "unloaded_jobs": []})
    monkeypatch.setattr(control, "mcp_processes", list)
    monkeypatch.setattr(control, "clients_status", lambda launcher=None: {})
    monkeypatch.setattr(
        control, "qdrant_status", lambda: {"running": True, "healthy": True, "label": "q"}
    )

    result = control.set_qwen("off")

    assert control.read_qwen_switch() == "off"
    assert "Full memory needs Qwen, but its switch is off" in result["drift"]


def test_set_qwen_reports_a_failure_without_losing_the_recorded_intent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    control = load_control(monkeypatch, tmp_path)

    def failing_start() -> None:
        raise control.ControlError("Qwen launcher is missing")

    monkeypatch.setattr(control, "start_qwen_if_needed", failing_start)
    monkeypatch.setattr(control, "status_payload", lambda: {"ok": True})

    result = control.set_qwen("on")

    assert control.read_qwen_switch() == "on"
    assert result["ok"] is False
    assert "Qwen launcher is missing" in result["error"]


def test_qwen_liveness_uses_the_port_when_http_probe_is_silent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A busy single-threaded server must not read as "already stopped"."""
    control = load_control(monkeypatch, tmp_path)
    monkeypatch.setattr(control, "port_listener_pids", lambda port: [555])
    monkeypatch.setattr(control, "qwen_status", lambda: {"running": False})

    assert control.qwen_is_up() is True


def test_terminate_pids_reports_signals_it_was_not_allowed_to_send(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    control = load_control(monkeypatch, tmp_path)

    def refuse(pid: int, signal_number: int) -> None:
        raise PermissionError(pid)

    monkeypatch.setattr(control.os, "kill", refuse)

    assert control.terminate_pids({4242}) == {4242}


def test_reconcile_records_what_it_found_before_repairing_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The failing state only exists in the run that fixes it, so log it there."""
    control = load_control(monkeypatch, tmp_path)
    monkeypatch.setattr(control, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(control, "RECONCILE_LOG", tmp_path / "logs/reconcile.log")
    control.write_mode("full")
    control.write_qwen_switch("off")
    monkeypatch.setattr(
        control,
        "boot_snapshot",
        lambda: {"mode": "full", "qwen_switch": "off", "qdrant_healthy": False,
                 "qwen_healthy": False, "mcp_processes": 0},
    )
    monkeypatch.setattr(control, "start_qdrant", lambda: None)
    monkeypatch.setattr(control, "url_ready", lambda url, timeout=1.5: True)
    monkeypatch.setattr(control, "qwen_status", lambda: {"healthy": False})
    monkeypatch.setattr(control, "status_payload", lambda: {"ok": True})

    control.reconcile()

    lines = (tmp_path / "logs/reconcile.log").read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[-1])
    assert entry["before"]["qdrant_healthy"] is False
    assert entry["reconciled"] == ["qdrant"]
    assert entry["after"]["qdrant_healthy"] is True


def test_reconcile_log_failure_never_breaks_the_repair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    control = load_control(monkeypatch, tmp_path)
    unwritable = tmp_path / "logs/reconcile.log"
    monkeypatch.setattr(control, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(control, "RECONCILE_LOG", unwritable)

    control.write_mode("off")
    monkeypatch.setattr(control, "boot_snapshot", lambda: {"mode": "off", "qwen_switch": "off"})
    monkeypatch.setattr(control, "status_payload", lambda: {"ok": True})

    def refuse(*args: object, **kwargs: object) -> None:
        raise OSError("read-only file system")

    # Patched last: it would otherwise break the setup writes above too.
    monkeypatch.setattr(control.Path, "mkdir", refuse)

    assert control.reconcile()["reconciled"] == []


def test_reconcile_starts_only_what_the_recorded_intent_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Opening the app must fill in a down component without re-picking the mode."""
    control = load_control(monkeypatch, tmp_path)
    control.write_mode("full")
    control.write_qwen_switch("on")
    started: list[str] = []
    monkeypatch.setattr(control, "url_ready", lambda url, timeout=1.5: False)
    monkeypatch.setattr(control, "start_qdrant", lambda: started.append("qdrant"))
    monkeypatch.setattr(control, "qwen_status", lambda: {"healthy": False, "running": False})
    monkeypatch.setattr(control, "start_qwen_if_needed", lambda: started.append("qwen"))
    monkeypatch.setattr(control, "status_payload", lambda: {"ok": True})

    result = control.reconcile()

    assert started == ["qdrant", "qwen"]
    assert result["reconciled"] == ["qdrant", "qwen"]
    assert control.read_mode() == "full"


def test_reconcile_is_a_no_op_when_everything_already_answers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    control = load_control(monkeypatch, tmp_path)
    control.write_mode("full")
    control.write_qwen_switch("on")
    monkeypatch.setattr(control, "url_ready", lambda url, timeout=1.5: True)
    monkeypatch.setattr(control, "qwen_status", lambda: {"healthy": True, "running": True})
    monkeypatch.setattr(control, "start_qdrant", lambda: pytest.fail("must not restart"))
    monkeypatch.setattr(control, "start_qwen_if_needed", lambda: pytest.fail("must not restart"))
    monkeypatch.setattr(control, "status_payload", lambda: {"ok": True})

    assert control.reconcile()["reconciled"] == []


def test_reconcile_never_stops_anything_in_off_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Opening a window is not a request to shut things down."""
    control = load_control(monkeypatch, tmp_path)
    control.write_mode("off")
    control.write_qwen_switch("off")
    monkeypatch.setattr(control, "url_ready", lambda url, timeout=1.5: True)
    monkeypatch.setattr(control, "qwen_status", lambda: {"healthy": True, "running": True})
    for name in ("stop_qdrant", "stop_qwen", "terminate_mcp_processes", "start_qdrant"):
        monkeypatch.setattr(
            control,
            name,
            lambda *a, step=name, **k: pytest.fail(f"{step} must not run"),
        )
    monkeypatch.setattr(control, "status_payload", lambda: {"ok": True})

    assert control.reconcile()["reconciled"] == []


def test_reconcile_reports_a_component_that_will_not_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    control = load_control(monkeypatch, tmp_path)
    control.write_mode("full")
    control.write_qwen_switch("off")
    monkeypatch.setattr(control, "url_ready", lambda url, timeout=1.5: False)

    def failing_qdrant() -> None:
        raise control.ControlError("Qdrant failed to start: health check failed")

    monkeypatch.setattr(control, "start_qdrant", failing_qdrant)
    monkeypatch.setattr(control, "status_payload", lambda: {"ok": True})

    result = control.reconcile()

    assert result["ok"] is False
    assert "Qdrant failed to start" in result["error"]
    assert result["reconciled"] == []


def test_launcher_refuses_to_spawn_mcp_while_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    launcher = load_launcher(monkeypatch, tmp_path)
    launcher.MODE_FILE.parent.mkdir(parents=True)
    launcher.MODE_FILE.write_text('{"mode":"off"}', encoding="utf-8")
    monkeypatch.setattr(launcher.os, "execve", lambda *args: pytest.fail("must not exec"))

    assert launcher.main() == 75


def test_launcher_passes_state_only_mode_to_mcp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    launcher = load_launcher(monkeypatch, tmp_path)
    launcher.MODE_FILE.parent.mkdir(parents=True)
    launcher.MODE_FILE.write_text('{"mode":"state-only"}', encoding="utf-8")
    captured: dict = {}

    def capture_exec(path: str, arguments: list[str], environment: dict[str, str]) -> None:
        captured.update(path=path, arguments=arguments, environment=environment)
        raise RuntimeError("captured")

    monkeypatch.setattr(launcher.os, "execve", capture_exec)

    with pytest.raises(RuntimeError, match="captured"):
        launcher.main()

    assert captured["environment"]["AGENT_MEMORY_MODE"] == "state-only"
    assert captured["arguments"][-3:] == ["python", "-m", "agent_memory_hub.mcp_server"]
