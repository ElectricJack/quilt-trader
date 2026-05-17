"""coord lifecycle commands: start, stop, restart, status, logs."""
from __future__ import annotations

import time

import click

from sdk.cli import process as proc
from sdk.cli.output import print_json, fail


@click.group("coord")
def coord_group() -> None:
    """Coordinator process lifecycle."""


@coord_group.command("start")
@click.option("--foreground", "-f", is_flag=True, default=False)
@click.option("--port", default=8000, type=int)
@click.option("--host", default="0.0.0.0",
              help="Bind interface (default 0.0.0.0 for WSL + Tailscale reachability).")
@click.pass_context
def coord_start(ctx, foreground, port, host):
    """Start the coordinator (daemonized by default)."""
    if foreground:
        import os
        import sys
        os.execvp(
            sys.executable,
            [sys.executable, "-m", "uvicorn",
             "--factory", "coordinator.main:create_app",
             "--host", host, "--port", str(port)],
        )
    existing = proc.read_pid()
    if existing is not None and proc.is_alive(existing):
        if proc.is_healthy_url(f"http://127.0.0.1:{port}/api/health"):
            click.echo(f"already running (pid={existing})")
            return
    try:
        pid = proc.start_coord_daemon(
            host=host,
            port=port,
            health_url=f"http://127.0.0.1:{port}/api/health",
        )
    except RuntimeError as e:
        fail(4, str(e))
    if ctx.obj.get("json_mode"):
        print_json({"ok": True, "pid": pid, "port": port})
    else:
        click.echo(f"coord started (pid={pid}, port={port})")


@coord_group.command("stop")
def coord_stop():
    """Stop the coordinator."""
    stopped = proc.stop_process()
    if not stopped:
        click.echo("not running")
        return
    click.echo("stopped")


@coord_group.command("restart")
@click.pass_context
def coord_restart(ctx):
    """Stop then start the coordinator."""
    proc.stop_process()
    ctx.invoke(coord_start)


@coord_group.command("status")
@click.option("--port", default=8000, type=int)
@click.pass_context
def coord_status(ctx, port):
    """Show coordinator status."""
    pid = proc.read_pid()
    alive = pid is not None and proc.is_alive(pid)
    healthy = proc.is_healthy_url(f"http://127.0.0.1:{port}/api/health")
    if alive and healthy:
        state = "running"
    elif alive and not healthy:
        state = "unhealthy"
    else:
        state = "stopped"

    payload = {
        "state": state,
        "pid": pid if alive else None,
        "port": port,
    }
    if ctx.obj.get("json_mode"):
        print_json(payload)
    else:
        for k, v in payload.items():
            click.echo(f"{k}: {v}")


@coord_group.command("logs")
@click.option("--follow", "-f", is_flag=True, default=False)
@click.option("-n", "lines", default=50, type=int)
def coord_logs(follow, lines):
    """Tail the coordinator log."""
    p = proc.log_path()
    if not p.exists():
        fail(2, f"no log file at {p}")
    if not follow:
        with open(p, "r") as f:
            all_lines = f.readlines()
        for line in all_lines[-lines:]:
            click.echo(line.rstrip())
        return
    with open(p, "r") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 4096))
        recent = f.readlines()[-lines:]
        for line in recent:
            click.echo(line.rstrip())
        try:
            while True:
                where = f.tell()
                line = f.readline()
                if not line:
                    time.sleep(0.2)
                    f.seek(where)
                else:
                    click.echo(line.rstrip())
        except KeyboardInterrupt:
            pass
