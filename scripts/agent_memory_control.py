#!/usr/bin/env python3
"""Unified lifecycle controller for the AgentNexus Memory macOS app."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VALID_MODES = {"off", "state-only", "full"}
VALID_SWITCHES = {"on", "off"}
PROJECT_ROOT = Path(
    os.environ.get("AGENTNEXUS_PROJECT_ROOT", str(Path.home() / "Src/AgentNexus"))
)
USER_HOME = Path(os.environ.get("AGENTNEXUS_HOME", str(Path.home())))
SUPPORT_DIR = Path(
    os.environ.get(
        "AGENTNEXUS_SUPPORT_DIR",
        str(USER_HOME / "Library/Application Support/AgentNexus Memory"),
    )
)
MODE_FILE = SUPPORT_DIR / "mode.json"
QWEN_OWNER_FILE = SUPPORT_DIR / "qwen-owner.json"
# Separate from ownership: this records whether the user *wants* Qwen up,
# which survives status refreshes and mode switches.
QWEN_SWITCH_FILE = SUPPORT_DIR / "qwen-switch.json"
QDRANT_LABEL = "com.agentnexus.qdrant"
QWEN_LABEL = "com.agentnexus.memory.qwen"
QWEN_MODEL = "mlx-community/Qwen3.8-27B-4bit"
QWEN_PORT = int(os.environ.get("AGENTNEXUS_QWEN_PORT", "8080"))
# A 27B MLX model can take minutes to load on a cold page cache; the old 90s
# budget expired mid-load and reported a failure for a server that was fine.
QWEN_READY_TIMEOUT = float(os.environ.get("AGENTNEXUS_QWEN_READY_TIMEOUT", "300"))
# How long a stopped Qwen must stay down before we believe it. Anything that
# comes back inside this window is being restarted by a supervisor, and
# saying so beats reporting a success the user can see is false.
QWEN_SETTLE_SECONDS = float(os.environ.get("AGENTNEXUS_QWEN_SETTLE", "5"))
QWEN_PROCESS_MARKERS = ("mlx_lm.server", "mlx_lm/server", "mlx_lm server", "mlx-lm")
LSOF_BIN = Path(os.environ.get("AGENTNEXUS_LSOF_BIN", "/usr/sbin/lsof"))
QWEN_BIN = Path(os.environ.get("AGENTNEXUS_QWEN_BIN", str(USER_HOME / ".local/bin/mlx_lm.server")))
QDRANT_HEALTH_URL = "http://127.0.0.1:6333/healthz"
LOG_DIR = Path(
    os.environ.get("AGENTNEXUS_LOG_DIR", str(USER_HOME / "Library/Logs/AgentNexus"))
)
# Reconcile runs the moment the app window opens, which is exactly the
# moment a post-reboot failure is on screen. Recording what it found there
# is the only way to see that state: asking a person to run a diagnostic
# before touching anything only works if they remember to.
RECONCILE_LOG = LOG_DIR / "reconcile.log"
RECONCILE_LOG_MAX_BYTES = 512 * 1024
QWEN_MODELS_URL = "http://127.0.0.1:8080/v1/models"
GUI_DOMAIN = f"gui/{os.getuid()}"
DEFAULT_APP_PATH = Path("/Applications/AgentNexus Memory.app")
APP_PATH = Path(os.environ.get("AGENTNEXUS_APP_PATH", str(DEFAULT_APP_PATH)))
DEFAULT_LAUNCHER = APP_PATH / "Contents/Resources/agent-memory-launcher"
UV_BIN = Path(os.environ.get("AGENTNEXUS_UV_BIN", "/opt/homebrew/bin/uv"))

# Read-only data browsing. SQLite is opened in ro mode and Qdrant is only ever
# scrolled; these commands never mutate state.
STATE_DB_PATH = PROJECT_ROOT / "data/project_state.db"
QDRANT_BASE_URL = "http://127.0.0.1:6333"
QDRANT_COLLECTION = "agent_memories"
COUNT_TABLES = ("state_items", "raw_events", "work_logs", "tasks", "decisions")


class ControlError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017 - macOS Python 3.9


def atomic_write_text(path: Path, content: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            os.chmod(temporary, path.stat().st_mode & 0o777)
        elif mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n", 0o600)


def run(
    arguments: list[str], *, check: bool = True, timeout: float = 30
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            arguments,
            check=check,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ControlError(f"Command failed: {' '.join(arguments)}: {exc}") from exc


def read_mode() -> str:
    try:
        mode = json.loads(MODE_FILE.read_text(encoding="utf-8")).get("mode")
    except (OSError, ValueError, TypeError):
        return "state-only"
    return mode if mode in VALID_MODES else "state-only"


def write_mode(mode: str) -> None:
    if mode not in VALID_MODES:
        raise ControlError(f"Unsupported mode: {mode}")
    atomic_write_json(MODE_FILE, {"mode": mode, "updated_at": now_iso()})


def read_qwen_switch() -> str:
    """Whether Qwen is switched on, defaulting to what the mode implies."""
    try:
        value = json.loads(QWEN_SWITCH_FILE.read_text(encoding="utf-8")).get("desired")
    except (OSError, ValueError, TypeError):
        return "on" if read_mode() == "full" else "off"
    return value if value in VALID_SWITCHES else "off"


def write_qwen_switch(desired: str) -> None:
    if desired not in VALID_SWITCHES:
        raise ControlError(f"Unsupported Qwen switch value: {desired}")
    atomic_write_json(QWEN_SWITCH_FILE, {"desired": desired, "updated_at": now_iso()})


def url_json(url: str, timeout: float = 1.5) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    return payload if isinstance(payload, dict) else None


def url_ready(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError):
        return False


def wait_until(predicate: Any, expected: bool, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if bool(predicate()) is expected:
            return True
        time.sleep(0.25)
    return bool(predicate()) is expected


def launchd_loaded(label: str) -> bool:
    result = run(["/bin/launchctl", "print", f"{GUI_DOMAIN}/{label}"], check=False)
    return result.returncode == 0


def qdrant_status() -> dict[str, Any]:
    return {
        "running": launchd_loaded(QDRANT_LABEL),
        "healthy": url_ready(QDRANT_HEALTH_URL),
        "label": QDRANT_LABEL,
    }


def start_qdrant() -> None:
    if url_ready(QDRANT_HEALTH_URL):
        return
    installer = PROJECT_ROOT / "scripts/install_qdrant_launch_agent.sh"
    if not installer.is_file():
        raise ControlError(f"Qdrant installer is missing: {installer}")
    run(["/bin/launchctl", "enable", f"{GUI_DOMAIN}/{QDRANT_LABEL}"], check=False)
    result = run([str(installer)], check=False, timeout=45)
    if result.returncode != 0 or not wait_until(
        lambda: url_ready(QDRANT_HEALTH_URL), True, 10
    ):
        detail = (result.stderr or result.stdout).strip()
        raise ControlError(f"Qdrant failed to start: {detail or 'health check failed'}")


def stop_qdrant() -> None:
    run(["/bin/launchctl", "disable", f"{GUI_DOMAIN}/{QDRANT_LABEL}"], check=False)
    run(["/bin/launchctl", "bootout", f"{GUI_DOMAIN}/{QDRANT_LABEL}"], check=False)
    if not wait_until(lambda: url_ready(QDRANT_HEALTH_URL), False, 8):
        raise ControlError("Qdrant is still answering after shutdown")


def qwen_status() -> dict[str, Any]:
    payload = url_json(QWEN_MODELS_URL)
    model_ids = []
    if payload and isinstance(payload.get("data"), list):
        model_ids = [str(item.get("id")) for item in payload["data"] if isinstance(item, dict)]
    owned = QWEN_OWNER_FILE.is_file()
    return {
        "running": payload is not None,
        "healthy": QWEN_MODEL in model_ids,
        "owned_by_app": owned,
        "desired": read_qwen_switch(),
        "model": QWEN_MODEL,
        "models": model_ids,
    }


def start_qwen_if_needed() -> None:
    status = qwen_status()
    if status["healthy"]:
        return
    if status["running"]:
        raise ControlError(f"Port 8080 is running without the required model {QWEN_MODEL}")
    if not QWEN_BIN.is_file():
        raise ControlError(f"Qwen launcher is missing: {QWEN_BIN}")
    # A previous shutdown can leave the label registered without an ownership
    # record. That leftover is ours, not a foreign job, so clear it instead of
    # refusing to start.
    run(["/bin/launchctl", "remove", QWEN_LABEL], check=False)
    wait_until(lambda: launchd_loaded(QWEN_LABEL), False, 8)
    run(
        [
            "/bin/launchctl",
            "submit",
            "-l",
            QWEN_LABEL,
            "--",
            str(QWEN_BIN),
            "--model",
            QWEN_MODEL,
        ]
    )
    atomic_write_json(
        QWEN_OWNER_FILE,
        {"label": QWEN_LABEL, "model": QWEN_MODEL, "started_at": now_iso()},
    )
    if not wait_until(lambda: bool(qwen_status()["healthy"]), True, QWEN_READY_TIMEOUT):
        run(["/bin/launchctl", "remove", QWEN_LABEL], check=False)
        QWEN_OWNER_FILE.unlink(missing_ok=True)
        raise ControlError(
            f"Qwen did not become ready within {QWEN_READY_TIMEOUT:.0f} seconds"
        )


def process_commands() -> dict[int, str]:
    """Map every visible pid to its full command line."""
    result = run(["/bin/ps", "-axo", "pid=,command="], check=False)
    commands: dict[int, str] = {}
    for line in result.stdout.splitlines():
        match = re.match(r"\s*(\d+)\s+(.*)", line)
        if match:
            commands[int(match.group(1))] = match.group(2)
    return commands


def port_listener_pids(port: int) -> list[int]:
    """Pids listening on a local TCP port, or an empty list when lsof is absent."""
    if not LSOF_BIN.is_file():
        return []
    result = run(
        [str(LSOF_BIN), "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], check=False
    )
    pids: set[int] = set()
    for token in result.stdout.split():
        try:
            pids.add(int(token))
        except ValueError:
            continue
    return sorted(pids)


def qwen_listeners() -> list[dict[str, Any]]:
    """Processes holding the Qwen port, annotated with how they were recognised."""
    commands = process_commands()
    return [
        {
            "pid": pid,
            "command": commands.get(pid, ""),
            "matches_command": any(
                marker in commands.get(pid, "") for marker in QWEN_PROCESS_MARKERS
            ),
        }
        for pid in port_listener_pids(QWEN_PORT)
    ]


def process_parents() -> dict[int, int]:
    """Map every visible pid to its parent pid."""
    result = run(["/bin/ps", "-axo", "pid=,ppid="], check=False)
    parents: dict[int, int] = {}
    for line in result.stdout.splitlines():
        match = re.match(r"\s*(\d+)\s+(\d+)", line)
        if match:
            parents[int(match.group(1))] = int(match.group(2))
    return parents


def describe_qwen_listeners() -> str:
    """Name the port holders *and their parents*.

    When a stopped server reappears, the parent is the thing to go look at, so
    the message has to carry it; a bare pid sends the user hunting.
    """
    entries = qwen_listeners()
    if not entries:
        return "unidentified process"
    parents = process_parents()
    commands = process_commands()
    described = []
    for item in entries:
        parent = parents.get(item["pid"])
        parent_command = commands.get(parent, "unknown") if parent is not None else "unknown"
        described.append(
            f"pid {item['pid']} [{item['command'] or 'unknown'}] "
            f"started by pid {parent} [{parent_command}]"
        )
    return "; ".join(described)


def launchd_job_for_pid(pid: int) -> str | None:
    """The launchd label currently owning a pid, from `launchctl list`.

    `launchctl procinfo` would be authoritative but needs root; the list output
    already carries the pid in its first column, which is enough and works as
    the logged-in user.
    """
    result = run(["/bin/launchctl", "list"], check=False)
    for line in result.stdout.splitlines():
        columns = line.split()
        if len(columns) >= 3 and columns[0].isdigit() and int(columns[0]) == pid:
            return columns[2]
    return None


def unload_launchd_job(label: str) -> None:
    run(["/bin/launchctl", "remove", label], check=False)
    run(["/bin/launchctl", "bootout", f"{GUI_DOMAIN}/{label}"], check=False)


def unload_qwen_supervisors(processes: list[dict[str, Any]]) -> list[str]:
    """Unload the launchd jobs behind the Qwen processes, returning their labels.

    Signalling a supervised process is pointless: launchd restarts it, which is
    exactly how a manually submitted job kept resurrecting Qwen after every
    "off". Apple's own jobs and GUI application jobs are never touched.
    """
    labels: list[str] = []
    for item in processes:
        label = launchd_job_for_pid(item["pid"])
        if not label or label in labels:
            continue
        if label.startswith(("com.apple.", "application.")):
            continue
        unload_launchd_job(label)
        labels.append(label)
    return labels


def qwen_is_up() -> bool:
    """Liveness from the port first, health from HTTP second.

    mlx-lm serves on a single thread: while it is generating, `/v1/models` can
    miss the 1.5s probe, and treating that as "already stopped" made the stop
    path return silently while the server was still resident.
    """
    if port_listener_pids(QWEN_PORT):
        return True
    return qwen_status()["running"]


def qwen_processes() -> list[dict[str, Any]]:
    """Processes serving the configured Qwen endpoint.

    Identity comes from the port first, not from argv text. The server can be
    spelled `mlx_lm.server`, `python -m mlx_lm server`, or launched through a
    wrapper, and matching command names alone missed every spelling but the
    first, which is how a manually started Qwen survived "turn everything off".
    A listener is only claimed when the endpoint reports the managed model or
    the command still looks like mlx-lm, so an unrelated service on the port is
    reported rather than killed.
    """
    entries = qwen_listeners()
    if entries:
        if not (qwen_status()["healthy"] or any(item["matches_command"] for item in entries)):
            return []
        return [{"pid": item["pid"], "command": item["command"]} for item in entries]
    # No lsof, or nothing listening: fall back to argv matching.
    return [
        {"pid": pid, "command": command}
        for pid, command in process_commands().items()
        if any(marker in command for marker in QWEN_PROCESS_MARKERS)
    ]


def stop_qwen() -> dict[str, Any]:
    """Stop the local Qwen server whoever started it.

    Returns ``{"pids": [...], "unloaded_jobs": [...]}`` so the caller can say
    what actually happened, including which launchd job had to be removed.

    Ownership decides *how* we stop it, never *whether*: refusing to touch a
    manually started server made "turn everything off" a lie, because the model
    stayed resident. Anything holding the port that is not recognisably a Qwen
    server is still left alone and reported rather than killed blindly.
    """
    label_loaded = launchd_loaded(QWEN_LABEL)
    empty: dict[str, Any] = {"pids": [], "unloaded_jobs": []}
    if not label_loaded and not qwen_is_up():
        QWEN_OWNER_FILE.unlink(missing_ok=True)
        return empty

    if label_loaded:
        run(["/bin/launchctl", "remove", QWEN_LABEL], check=False)
        run(["/bin/launchctl", "bootout", f"{GUI_DOMAIN}/{QWEN_LABEL}"], check=False)
        wait_until(lambda: launchd_loaded(QWEN_LABEL), False, 8)
    QWEN_OWNER_FILE.unlink(missing_ok=True)

    if wait_until(qwen_is_up, False, 5):
        return empty

    processes = qwen_processes()
    if not processes:
        raise ControlError(
            f"Port {QWEN_PORT} is held by something that is not the managed Qwen, "
            f"so it was left running: {describe_qwen_listeners()}"
        )

    # Unload before signalling: a supervised process comes straight back.
    unloaded = unload_qwen_supervisors(processes)
    if unloaded:
        wait_until(
            lambda: any(process_exists(item["pid"]) for item in processes), False, 8
        )
    remaining = {item["pid"] for item in processes if process_exists(item["pid"])}
    refused = terminate_pids(remaining) if remaining else set()
    report = {"pids": [item["pid"] for item in processes], "unloaded_jobs": unloaded}

    if not wait_until(qwen_is_up, False, 8):
        detail = f"; not allowed to signal {sorted(refused)}" if refused else ""
        raise ControlError(
            f"Qwen still holds port {QWEN_PORT} after shutdown: "
            f"{describe_qwen_listeners()}{detail}"
        )
    if wait_until(qwen_is_up, True, QWEN_SETTLE_SECONDS):
        raise ControlError(
            f"Qwen was stopped but came back within {QWEN_SETTLE_SECONDS:.0f}s, so "
            f"something is restarting it: {describe_qwen_listeners()}"
        )
    return report


def mcp_processes() -> list[dict[str, Any]]:
    result = run(["/bin/ps", "-axo", "pid=,ppid=,rss=,command="], check=False)
    processes: list[dict[str, Any]] = []
    target = str(PROJECT_ROOT)
    for line in result.stdout.splitlines():
        match = re.match(r"\s*(\d+)\s+(\d+)\s+(\d+)\s+(.*)", line)
        if not match:
            continue
        pid, ppid, rss, command = match.groups()
        if "agent_memory_hub.mcp_server" not in command or target not in command:
            continue
        processes.append(
            {
                "pid": int(pid),
                "ppid": int(ppid),
                "rss_kib": int(rss),
                "command": command,
            }
        )
    return processes


def terminate_pids(pids: set[int]) -> set[int]:
    """Signal pids, returning the ones we were not allowed to signal.

    A refused signal used to escape as a bare PermissionError and take the whole
    controller down with it, which surfaced as an unreadable status instead of
    "this process is not yours".
    """
    pids = {pid for pid in pids if pid != os.getpid()}
    refused: set[int] = set()
    for pid in sorted(pids, reverse=True):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            refused.add(pid)
    pids -= refused
    deadline = time.monotonic() + 6
    while pids and time.monotonic() < deadline:
        pids = {pid for pid in pids if process_exists(pid)}
        if pids:
            time.sleep(0.2)
    for pid in sorted(pids, reverse=True):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            refused.add(pid)
    return refused


def terminate_mcp_processes() -> list[int]:
    processes = mcp_processes()
    terminate_pids({item["pid"] for item in processes})
    return [item["pid"] for item in processes]


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def client_paths() -> dict[str, Path]:
    return {
        "codex": USER_HOME / ".codex/config.toml",
        "workbuddy": USER_HOME / ".workbuddy/mcp.json",
        "codebuddy": USER_HOME / ".codebuddy/.mcp.json",
        "claude": USER_HOME / "Library/Application Support/Claude/claude_desktop_config.json",
    }


def back_up_configs(paths: dict[str, Path]) -> Path:
    stamp = datetime.now(timezone.utc).strftime(  # noqa: UP017 - macOS Python 3.9
        "%Y%m%d-%H%M%S-%f"
    )
    destination = SUPPORT_DIR / "config-backups" / stamp
    destination.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, Any] = {"created_at": now_iso(), "files": {}}
    for name, path in paths.items():
        if not path.is_file():
            manifest["files"][name] = {"path": str(path), "exists": False}
            continue
        backup = destination / f"{name}{path.suffix or '.config'}"
        shutil.copy2(path, backup)
        manifest["files"][name] = {
            "path": str(path),
            "exists": True,
            "backup": backup.name,
        }
    atomic_write_json(destination / "manifest.json", manifest)
    return destination


def configure_codex(path: Path, launcher: Path) -> None:
    if not path.is_file():
        raise ControlError(f"Codex config is missing: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    header = "[mcp_servers.agent-memory]"
    try:
        start = lines.index(header)
    except ValueError as exc:
        raise ControlError("Codex agent-memory section is missing") from exc
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("["):
            end = index
            break
    section = lines[start + 1 : end]
    replacements = {
        "command": f'command = "{launcher}"',
        "args": "args = []",
        "enabled": "enabled = true",
    }
    found: set[str] = set()
    rewritten: list[str] = []
    for line in section:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in replacements:
            if key not in found:
                rewritten.append(replacements[key])
                found.add(key)
            continue
        rewritten.append(line)
    for key in ("command", "args", "enabled"):
        if key not in found:
            rewritten.append(replacements[key])
    lines[start + 1 : end] = rewritten
    atomic_write_text(path, "\n".join(lines) + "\n")


def configure_json_client(path: Path, launcher: Path, include_type: bool) -> None:
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise ControlError(f"Invalid JSON config {path}: {exc}") from exc
    else:
        payload = {}
    if not isinstance(payload, dict):
        raise ControlError(f"MCP config must be a JSON object: {path}")
    servers = payload.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ControlError(f"mcpServers must be an object: {path}")
    config: dict[str, Any] = {
        "command": str(launcher),
        "args": [],
        "env": {"MEM0_TELEMETRY": "false"},
    }
    if include_type:
        config["type"] = "stdio"
    servers["agent-memory"] = config
    disabled = payload.get("disabledMcpServers")
    if isinstance(disabled, list):
        payload["disabledMcpServers"] = [item for item in disabled if item != "agent-memory"]
    atomic_write_json(path, payload)


def install_client_configs(launcher: Path) -> dict[str, Any]:
    if not launcher.is_file():
        raise ControlError(f"Installed launcher is missing: {launcher}")
    paths = client_paths()
    current = clients_status(launcher)
    if all(item["configured"] for item in current.values()):
        if not MODE_FILE.is_file():
            write_mode("full")
        return {"backup": None, "clients": current}
    backup = back_up_configs(paths)
    configure_codex(paths["codex"], launcher)
    configure_json_client(paths["workbuddy"], launcher, include_type=True)
    configure_json_client(paths["codebuddy"], launcher, include_type=True)
    configure_json_client(paths["claude"], launcher, include_type=False)
    if not MODE_FILE.is_file():
        write_mode("full")
    return {"backup": str(backup), "clients": clients_status(launcher)}


def codex_uses_launcher(path: Path, launcher: Path) -> bool:
    if not path.is_file():
        return False
    content = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"(?ms)^\[mcp_servers\.agent-memory\]\s*$.*?(?=^\[|\Z)"
    )
    match = pattern.search(content)
    return bool(match and f'command = "{launcher}"' in match.group(0))


def json_uses_launcher(path: Path, launcher: Path) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        config = payload["mcpServers"]["agent-memory"]
    except (OSError, ValueError, TypeError, KeyError):
        return False
    return isinstance(config, dict) and config.get("command") == str(launcher)


def clients_status(launcher: Path = DEFAULT_LAUNCHER) -> dict[str, Any]:
    paths = client_paths()
    return {
        "codex": {"configured": codex_uses_launcher(paths["codex"], launcher)},
        "workbuddy": {"configured": json_uses_launcher(paths["workbuddy"], launcher)},
        "codebuddy": {"configured": json_uses_launcher(paths["codebuddy"], launcher)},
        "claude": {"configured": json_uses_launcher(paths["claude"], launcher)},
    }


def mcp_readiness(
    mode: str, clients: dict[str, Any], launcher: Path = DEFAULT_LAUNCHER
) -> dict[str, Any]:
    """Describe whether clients can start MCP, independently of live connections.

    MCP uses stdio, so there is intentionally no shared daemon for this app to
    keep alive. A zero process count means "no agent client connected yet", not
    "startup failed". Readiness is the durable post-reboot condition we own.
    """
    reasons: list[str] = []
    enabled = mode != "off"
    if enabled and not launcher.is_file():
        reasons.append(f"MCP launcher is missing: {launcher}")
    if enabled and not os.access(launcher, os.X_OK):
        reasons.append(f"MCP launcher is not executable: {launcher}")
    if enabled and not PROJECT_ROOT.is_dir():
        reasons.append(f"AgentNexus project is missing: {PROJECT_ROOT}")
    if enabled and not UV_BIN.is_file():
        reasons.append(f"uv is missing: {UV_BIN}")
    if enabled and any(not value["configured"] for value in clients.values()):
        reasons.append("One or more clients are not using the unified launcher")
    return {"enabled": enabled, "ready": enabled and not reasons, "reasons": reasons}


def status_payload(launcher: Path = DEFAULT_LAUNCHER) -> dict[str, Any]:
    processes = mcp_processes()
    clients = clients_status(launcher)
    mode = read_mode()
    mcp = mcp_readiness(mode, clients, launcher)
    qdrant = qdrant_status()
    qwen = qwen_status()
    expected_qdrant = mode == "full"
    drift: list[str] = []
    if any(not value["configured"] for value in clients.values()):
        drift.append("One or more clients are not using the unified launcher")
    for reason in mcp["reasons"]:
        if reason not in drift:
            drift.append(reason)
    if expected_qdrant != bool(qdrant["healthy"]):
        drift.append("Qdrant state does not match the selected mode")
    if mode == "off" and processes:
        drift.append("MCP processes remain while mode is off")
    if qwen["desired"] == "on" and not qwen["running"]:
        drift.append("Qwen is switched on but is not answering")
    if qwen["desired"] == "off" and qwen["running"]:
        drift.append("Qwen is still running while its switch is off")
    if mode == "full" and qwen["desired"] == "off":
        drift.append("Full memory needs Qwen, but its switch is off")
    return {
        "ok": not drift,
        "mode": mode,
        "components": {
            "mcp": {
                "running": len(processes),
                "rss_mib": round(sum(item["rss_kib"] for item in processes) / 1024, 1),
                **mcp,
            },
            "project_state": {"available": mode != "off"},
            "mem0": {"enabled": mode == "full"},
            "qdrant": qdrant,
            "qwen": qwen,
        },
        "clients": clients,
        "drift": drift,
        "updated_at": now_iso(),
    }


def set_mode(mode: str) -> dict[str, Any]:
    """Apply a mode, recording it even when an optional component misbehaves.

    The mode file is the gate the MCP launcher reads, so it is written first.
    Previously it was written only after every component had started, which
    meant one slow or stuck Qwen left the gate closed and every client failed
    to spawn the MCP server with "AgentNexus Memory is off". Component failures
    are now reported as warnings against an applied mode instead.
    """
    if mode not in VALID_MODES:
        raise ControlError(f"Unsupported mode: {mode}")
    previous = read_mode()
    stopped_pids: list[int] = []
    warnings: list[str] = []

    def attempt(step: Any) -> None:
        try:
            step()
        except ControlError as exc:
            warnings.append(str(exc))

    write_mode(mode)
    if mode == "full":
        write_qwen_switch("on")
        attempt(start_qdrant)
        attempt(start_qwen_if_needed)
    else:
        stopped_pids = terminate_mcp_processes()
        attempt(stop_qdrant)
        write_qwen_switch("off")
        attempt(stop_qwen)

    payload = status_payload()
    payload.update(
        {
            "previous_mode": previous,
            "stopped_mcp_pids": stopped_pids,
            "warnings": warnings,
        }
    )
    if warnings:
        payload["ok"] = False
        payload["error"] = "; ".join(warnings)
    return payload


def append_reconcile_log(entry: dict[str, Any]) -> None:
    """Append one JSON line. Never let logging break the thing being logged."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        if RECONCILE_LOG.is_file() and RECONCILE_LOG.stat().st_size > RECONCILE_LOG_MAX_BYTES:
            RECONCILE_LOG.replace(RECONCILE_LOG.with_suffix(".log.1"))
        with RECONCILE_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def probe(label: str, reader: Any, default: Any = None) -> Any:
    """Evaluate one diagnostic probe, substituting a default if it blows up.

    A snapshot exists to explain a failure; it must never become one. Any probe
    can fail on a machine that is missing a tool, so each one is isolated.
    """
    try:
        return reader()
    except Exception:  # noqa: BLE001 - a diagnostic must not raise, whatever breaks
        return default


def boot_snapshot() -> dict[str, Any]:
    """Everything cheap that explains why a component is not answering."""
    plist = USER_HOME / "Library/LaunchAgents/com.agentnexus.qdrant.plist"
    mode = probe("mode", read_mode, "unknown")
    clients = probe("clients", clients_status, {})
    readiness = probe(
        "mcp_readiness",
        lambda: mcp_readiness(mode, clients),
        {"enabled": mode != "off", "ready": False, "reasons": ["probe failed"]},
    )
    return {
        "mode": mode,
        "mcp_ready": readiness["ready"],
        "mcp_readiness_reasons": readiness["reasons"],
        "qwen_switch": probe("qwen_switch", read_qwen_switch, "unknown"),
        "qdrant_healthy": probe("qdrant", lambda: url_ready(QDRANT_HEALTH_URL), False),
        "qdrant_launchd_loaded": probe("qdrant_job", lambda: launchd_loaded(QDRANT_LABEL)),
        "qdrant_plist_present": probe("qdrant_plist", plist.is_file),
        "qdrant_listeners": probe("qdrant_port", lambda: port_listener_pids(6333), []),
        "qwen_healthy": probe("qwen", lambda: qwen_status()["healthy"], False),
        "qwen_listeners": probe("qwen_port", lambda: port_listener_pids(QWEN_PORT), []),
        "mcp_processes": probe("mcp", lambda: len(mcp_processes())),
        "uptime": probe(
            "uptime", lambda: run(["/usr/bin/uptime"], check=False).stdout.strip()
        ),
    }


def reconcile() -> dict[str, Any]:
    """Start whatever the recorded intent says should be running, and nothing else.

    Opening the app used to start nothing at all: the controller was only ever
    asked for `status`, so a machine that came back from a reboot with the
    components down still displayed "完整记忆" while nothing ran, and the user
    had to re-pick the mode by hand to trigger a real `set-mode`.

    This only ever *starts*. Stopping on launch would be a surprising thing for
    opening a window to do, and a mode that is already off needs no action --
    the drift list already reports anything left running.
    """
    before = boot_snapshot()
    mode = before["mode"]
    warnings: list[str] = []
    started: list[str] = []

    def attempt(name: str, step: Any) -> None:
        try:
            step()
            started.append(name)
        except ControlError as exc:
            warnings.append(str(exc))

    if mode == "full" and not before["qdrant_healthy"]:
        attempt("qdrant", start_qdrant)
    if before["qwen_switch"] == "on" and not before["qwen_healthy"]:
        attempt("qwen", start_qwen_if_needed)
    if (
        mode != "off"
        and not before.get("mcp_ready", True)
        and DEFAULT_LAUNCHER.is_file()
    ):
        attempt("mcp-launcher", lambda: install_client_configs(DEFAULT_LAUNCHER))

    payload = status_payload()
    payload.update({"mode": mode, "reconciled": started, "warnings": warnings})
    append_reconcile_log(
        {
            "at": now_iso(),
            "before": before,
            "reconciled": started,
            "warnings": warnings,
            "after": {
                "qdrant_healthy": url_ready(QDRANT_HEALTH_URL),
                "qwen_healthy": qwen_status()["healthy"],
            },
        }
    )
    if warnings:
        payload["ok"] = False
        payload["error"] = "; ".join(warnings)
    return payload


def set_qwen(desired: str) -> dict[str, Any]:
    """Start or stop Qwen on its own, independent of the selected mode.

    Qdrant is deliberately untouched here: this switch owns port 8080 and
    nothing else. Turning Qwen off while the mode is full is allowed but shows
    up as drift, because Mem0 extraction has no model to call.
    """
    if desired not in VALID_SWITCHES:
        raise ControlError(f"Unsupported Qwen switch value: {desired}")
    previous = read_qwen_switch()
    warnings: list[str] = []
    stopped_pids: list[int] = []
    unloaded_jobs: list[str] = []
    write_qwen_switch(desired)
    try:
        if desired == "on":
            start_qwen_if_needed()
        else:
            report = stop_qwen()
            stopped_pids = report["pids"]
            unloaded_jobs = report["unloaded_jobs"]
    except ControlError as exc:
        warnings.append(str(exc))

    payload = status_payload()
    payload.update(
        {
            "previous_qwen_switch": previous,
            "stopped_qwen_pids": stopped_pids,
            "unloaded_launchd_jobs": unloaded_jobs,
            "warnings": warnings,
        }
    )
    if warnings:
        payload["ok"] = False
        payload["error"] = "; ".join(warnings)
    return payload


# --- Read-only data browsing: list-projects / list-memories ----------------
#
# Powers the "数据浏览" section of the macOS app. Strictly read-only: the
# SQLite connection is opened with mode=ro and Qdrant is only scrolled. When
# Qdrant is down the SQLite half still answers and the payload carries
# qdrant_healthy=false instead of failing the whole call.


def _open_state_db() -> sqlite3.Connection:
    if not STATE_DB_PATH.exists():
        raise ControlError(f"State database not found: {STATE_DB_PATH}")
    connection = sqlite3.connect(
        f"file:{STATE_DB_PATH}?mode=ro", uri=True, timeout=2.0
    )
    connection.row_factory = sqlite3.Row
    return connection


def _table_counts(
    connection: sqlite3.Connection, project_id: str
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in COUNT_TABLES:
        row = connection.execute(
            f"SELECT COUNT(*) AS n FROM {table} WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        counts[table] = int(row["n"])
    return counts


def qdrant_healthy(timeout: float = 1.5) -> bool:
    """Qdrant is reachable, so memory counts are trustworthy."""
    try:
        with urllib.request.urlopen(
            urllib.request.Request(QDRANT_HEALTH_URL, method="GET"),
            timeout=timeout,
        ) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def _scroll_memories(timeout: float = 3.0) -> list[dict[str, Any]]:
    """All points in the agent_memories collection (payload only, no vectors)."""
    points: list[dict[str, Any]] = []
    offset: Any = None
    while True:
        body: dict[str, Any] = {
            "limit": 100,
            "with_payload": True,
            "with_vector": False,
        }
        if offset is not None:
            body["offset"] = offset
        request = urllib.request.Request(
            f"{QDRANT_BASE_URL}/collections/{QDRANT_COLLECTION}/points/scroll",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        result = data.get("result", {})
        points.extend(result.get("points", []))
        offset = result.get("next_page_offset")
        if not offset:
            break
    return points


def _memories_by_project() -> dict[str, int]:
    """project_id -> memory count, across all Qdrant points."""
    tally: dict[str, int] = {}
    for point in _scroll_memories():
        project_id = (point.get("payload") or {}).get("project_id")
        if project_id:
            tally[project_id] = tally.get(project_id, 0) + 1
    return tally


def list_projects_payload() -> dict[str, Any]:
    """Registered projects (SQLite is authoritative) plus Qdrant memory counts."""
    healthy = qdrant_healthy()
    memories_by_project: dict[str, int] = {}
    if healthy:
        try:
            memories_by_project = _memories_by_project()
        except (OSError, ValueError, TypeError, KeyError, urllib.error.URLError):
            healthy = False

    connection = _open_state_db()
    try:
        rows = connection.execute(
            "SELECT id, name, repo_path, created_at FROM projects ORDER BY id"
        ).fetchall()
        projects: list[dict[str, Any]] = []
        for row in rows:
            project_id = row["id"]
            project: dict[str, Any] = {
                "id": project_id,
                "name": row["name"],
                "repo_path": row["repo_path"],
                "counts": _table_counts(connection, project_id),
            }
            if healthy:
                project["memories"] = memories_by_project.get(project_id, 0)
            projects.append(project)
        registered = {row["id"] for row in rows}
    finally:
        connection.close()

    orphan_memories = {
        project_id: count
        for project_id, count in sorted(memories_by_project.items())
        if project_id not in registered
    }
    payload: dict[str, Any] = {
        "ok": True,
        "qdrant_healthy": healthy,
        "projects": projects,
        "updated_at": now_iso(),
    }
    if orphan_memories:
        payload["orphan_memories"] = orphan_memories
    return payload


def list_memories_payload(project_id: str | None) -> dict[str, Any]:
    """Semantic memories for one project, or all projects when omitted."""
    if project_id is not None:
        connection = _open_state_db()
        try:
            exists = connection.execute(
                "SELECT 1 FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        finally:
            connection.close()
        if exists is None:
            raise ControlError(f"Unknown project_id: {project_id}")

    healthy = qdrant_healthy()
    memories: list[dict[str, Any]] = []
    if healthy:
        try:
            for point in _scroll_memories():
                payload = point.get("payload") or {}
                point_project = payload.get("project_id")
                if project_id is not None and point_project != project_id:
                    continue
                memory: dict[str, Any] = {
                    "memory_id": point.get("id"),
                    "text": payload.get("data")
                    or payload.get("memory")
                    or "",
                    "created_at": payload.get("created_at"),
                }
                if project_id is None:
                    memory["project_id"] = point_project
                memories.append(memory)
        except (OSError, ValueError, TypeError, KeyError, urllib.error.URLError):
            healthy = False
    memories.sort(key=lambda item: item.get("created_at") or "")

    payload: dict[str, Any] = {
        "ok": True,
        "qdrant_healthy": healthy,
        "memories": memories,
        "updated_at": now_iso(),
    }
    if project_id is not None:
        payload["project_id"] = project_id
    return payload


def emit(payload: dict[str, Any], compact: bool) -> None:
    if compact:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit compact JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")
    mode_parser = subparsers.add_parser("set-mode")
    mode_parser.add_argument("mode", choices=sorted(VALID_MODES))
    qwen_parser = subparsers.add_parser("set-qwen")
    qwen_parser.add_argument("desired", choices=sorted(VALID_SWITCHES))
    install_parser = subparsers.add_parser("install-clients")
    install_parser.add_argument("--launcher", type=Path, default=DEFAULT_LAUNCHER)
    subparsers.add_parser("reconcile")
    subparsers.add_parser("list-projects")
    memories_parser = subparsers.add_parser("list-memories")
    memories_parser.add_argument("project_id", nargs="?")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "status":
            payload = status_payload()
        elif args.command == "set-mode":
            payload = set_mode(args.mode)
        elif args.command == "set-qwen":
            payload = set_qwen(args.desired)
        elif args.command == "install-clients":
            payload = {"ok": True, **install_client_configs(args.launcher)}
        elif args.command == "reconcile":
            payload = reconcile()
        elif args.command == "list-projects":
            payload = list_projects_payload()
        elif args.command == "list-memories":
            payload = list_memories_payload(args.project_id)
        else:
            raise ControlError(f"Unknown command: {args.command}")
    except ControlError as exc:
        emit({"ok": False, "error": str(exc), "updated_at": now_iso()}, args.json)
        return 1
    emit(payload, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
