"""Start and stop monitor.py for deep-work sessions."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
PID_FILE = PROJECT_DIR / ".monitor.pid"
LOG_FILE = PROJECT_DIR / "monitor.log"


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def is_monitor_running() -> bool:
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
    except ValueError:
        return False
    if not _pid_is_alive(pid):
        PID_FILE.unlink(missing_ok=True)
        return False
    return True


def start_monitor() -> str:
    if is_monitor_running():
        pid = int(PID_FILE.read_text().strip())
        return f"Monitor already running (pid {pid})."

    env = os.environ.copy()
    adb_dir = env.get("DEEPMODE_ADB_DIR", "").strip()
    if adb_dir:
        env["PATH"] = adb_dir + os.pathsep + env.get("PATH", "")

    log_handle = LOG_FILE.open("a", encoding="utf-8")
    log_handle.write("\n--- monitor started by start_deep_work ---\n")
    log_handle.flush()

    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

    proc = subprocess.Popen(
        [sys.executable, str(PROJECT_DIR / "monitor.py")],
        cwd=PROJECT_DIR,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    return f"Monitor started (pid {proc.pid}). Logs: {LOG_FILE.name}"


def stop_monitor() -> str:
    if not PID_FILE.exists():
        return "Monitor was not started by Deepmode (no pid file)."

    try:
        pid = int(PID_FILE.read_text().strip())
    except ValueError:
        PID_FILE.unlink(missing_ok=True)
        return "Monitor stop skipped (invalid pid file)."

    if not _pid_is_alive(pid):
        PID_FILE.unlink(missing_ok=True)
        return "Monitor already stopped."

    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            shell=False,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        try:
            os.kill(pid, 9)
        except OSError:
            pass

    PID_FILE.unlink(missing_ok=True)
    return f"Monitor stopped (pid {pid})."
