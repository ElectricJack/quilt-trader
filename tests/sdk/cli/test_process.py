import os
import subprocess
import time
from pathlib import Path
import pytest
from sdk.cli import process as p


def test_read_pid_returns_none_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setenv("QUILT_HOME", str(tmp_path))
    assert p.read_pid() is None


def test_read_pid_returns_int_when_file_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("QUILT_HOME", str(tmp_path))
    p._pid_path().write_text("12345")
    assert p.read_pid() == 12345


def test_is_alive_returns_true_for_own_process():
    assert p.is_alive(os.getpid())


def test_is_alive_returns_false_for_nonexistent_pid():
    # PID 99999999 is almost certainly not in use
    assert not p.is_alive(99_999_999)


def test_stop_process_with_no_pid_is_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("QUILT_HOME", str(tmp_path))
    assert p.stop_process() is False


def test_stop_process_kills_running_subprocess(tmp_path, monkeypatch):
    monkeypatch.setenv("QUILT_HOME", str(tmp_path))
    # Spawn a long-running subprocess in its own process group
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        p._pid_path().write_text(str(proc.pid))
        result = p.stop_process(timeout=2.0)
        # Should be dead
        proc.wait(timeout=3)
        assert proc.returncode is not None
        assert result is True
    finally:
        try:
            proc.kill()
        except Exception:
            pass
    assert not p._pid_path().exists()
