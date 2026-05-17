"""quilt doctor — diagnose common breakage."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import click
import httpx

from sdk.cli.config import resolve_coordinator_url, quilt_home
from sdk.cli.output import print_json, print_table
from sdk.cli import process as proc


def _check(name: str, status: str, message: str) -> dict:
    return {"name": name, "status": status, "message": message}


def _check_config_exists() -> dict:
    p = quilt_home() / "config.yaml"
    if p.exists():
        return _check("config_exists", "PASS", str(p))
    return _check("config_exists", "FAIL", f"missing {p}; run `quilt init`")


def _check_dashboard_built() -> dict:
    p = Path("dashboard/dist/index.html")
    if p.exists():
        return _check("dashboard_built", "PASS", str(p))
    return _check("dashboard_built", "WARN", "run `quilt dashboard build`")


def _check_coord_pid() -> dict:
    pid = proc.read_pid()
    if pid is None:
        return _check("coord_pid", "PASS", "no PID file (coord not started by CLI)")
    if proc.is_alive(pid):
        return _check("coord_pid", "PASS", f"pid {pid} alive")
    return _check("coord_pid", "FAIL", f"stale PID file (pid {pid} dead) — run `quilt coord stop`")


def _check_coord_reachable(url: str) -> dict:
    try:
        r = httpx.get(f"{url}/api/health", timeout=2.0)
        if 200 <= r.status_code < 300:
            return _check("coord_reachable", "PASS", url)
    except Exception:
        pass
    return _check("coord_reachable", "WARN", f"{url} not reachable (run `quilt coord start`)")


def _check_db_reachable() -> dict:
    import sys as _sys
    r = subprocess.run(
        [_sys.executable, "-m", "alembic", "-c", "alembic.ini", "current"],
        capture_output=True,
    )
    if r.returncode == 0:
        msg = r.stdout.decode(errors="replace").strip() or "ok"
        return _check("db_reachable", "PASS", msg)
    return _check("db_reachable", "FAIL", r.stderr.decode(errors="replace").strip())


def _check_disk_space() -> dict:
    total, used, free = shutil.disk_usage(Path("."))
    free_gb = free / (1024**3)
    if free_gb >= 1.0:
        return _check("disk_space", "PASS", f"{free_gb:.1f} GB free")
    return _check("disk_space", "WARN", f"only {free_gb:.2f} GB free")


def _coord_diagnostics_checks(url: str) -> list[dict]:
    try:
        r = httpx.get(f"{url}/api/diagnostics", timeout=5.0)
        if 200 <= r.status_code < 300:
            data = r.json()
            return data.get("checks", [])
    except Exception:
        pass
    return [_check("coord_diagnostics", "WARN", "coord not reachable — runtime checks skipped")]


@click.command("doctor")
@click.pass_context
def doctor_cmd(ctx):
    """Diagnose Quilt installation health."""
    coord_url = resolve_coordinator_url(ctx.obj.get("coord_url"))

    checks: list[dict] = []
    checks.append(_check_config_exists())
    checks.append(_check_dashboard_built())
    checks.append(_check_coord_pid())
    checks.append(_check_coord_reachable(coord_url))
    checks.append(_check_db_reachable())
    checks.append(_check_disk_space())
    checks.extend(_coord_diagnostics_checks(coord_url))

    any_fail = any(c["status"] == "FAIL" for c in checks)
    any_warn = any(c["status"] == "WARN" for c in checks)
    ok = not any_fail and not any_warn

    payload = {"ok": ok, "checks": checks}
    if ctx.obj.get("json_mode"):
        print_json(payload)
    else:
        print_table(checks, columns=["name", "status", "message"])

    if any_fail:
        raise SystemExit(2)
    if any_warn:
        raise SystemExit(1)
