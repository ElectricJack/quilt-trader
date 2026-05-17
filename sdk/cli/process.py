"""PID file + log file management for the coordinator process.

`start_coord_daemon()` spawns uvicorn detached with stdout/stderr → log file.
`stop_process()` SIGTERMs the PID with SIGKILL escalation.
`read_pid()` / `is_alive()` / `is_healthy_url()` are inspection helpers.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import httpx

from sdk.cli.config import quilt_log_dir, quilt_run_dir

logger = logging.getLogger(__name__)


def _pid_path() -> Path:
    return quilt_run_dir() / "coord.pid"


def _log_path() -> Path:
    return quilt_log_dir() / "coord.log"


def log_path() -> Path:
    return _log_path()


def read_pid() -> Optional[int]:
    p = _pid_path()
    if not p.exists():
        return None
    try:
        return int(p.read_text().strip())
    except Exception:
        return None


def write_pid(pid: int) -> None:
    _pid_path().write_text(str(pid))


def remove_pid() -> None:
    p = _pid_path()
    if p.exists():
        p.unlink()


def is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def is_healthy_url(url: str, timeout: float = 2.0) -> bool:
    try:
        r = httpx.get(url, timeout=timeout)
        return 200 <= r.status_code < 300
    except Exception:
        return False


def start_coord_daemon(*, host: str = "127.0.0.1", port: int = 8000,
                       health_url: str = "http://127.0.0.1:8000/api/health",
                       startup_timeout: float = 30.0) -> int:
    """Spawn uvicorn detached. Return the child PID after health check passes.

    Raises RuntimeError if health check doesn't pass within startup_timeout.
    """
    log_p = _log_path()
    log_p.parent.mkdir(parents=True, exist_ok=True)
    log_fd = open(log_p, "ab")
    cmd = [
        sys.executable, "-m", "uvicorn", "coordinator.main:app",
        "--host", host, "--port", str(port),
    ]
    proc = subprocess.Popen(
        cmd, stdout=log_fd, stderr=log_fd, stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    log_fd.close()
    write_pid(proc.pid)

    deadline = time.monotonic() + startup_timeout
    while time.monotonic() < deadline:
        if not is_alive(proc.pid):
            remove_pid()
            raise RuntimeError(
                f"uvicorn died during startup; see {log_p}"
            )
        if is_healthy_url(health_url):
            return proc.pid
        time.sleep(0.2)
    # Timeout — kill the child
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        pass
    remove_pid()
    raise RuntimeError(
        f"coordinator did not become healthy at {health_url} within {startup_timeout}s"
    )


def stop_process(timeout: float = 10.0) -> bool:
    """Stop the coord process by PID. Returns True if a process was stopped."""
    pid = read_pid()
    if pid is None:
        return False
    if not is_alive(pid):
        remove_pid()
        return False
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        remove_pid()
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_alive(pid):
            remove_pid()
            return True
        time.sleep(0.1)
    # SIGKILL fallback
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except Exception:
        pass
    remove_pid()
    return True
