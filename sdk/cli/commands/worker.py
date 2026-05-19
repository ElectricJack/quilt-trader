"""worker commands."""
from __future__ import annotations

import asyncio

import click

from sdk.cli.client import CoordinatorClient, CLIError
from sdk.cli.config import resolve_coordinator_url
from sdk.cli.output import print_json, print_table, fail
from sdk.cli.resolve import _short_id, resolve_id


def _client(ctx) -> CoordinatorClient:
    return CoordinatorClient(resolve_coordinator_url(ctx.obj.get("coord_url")))


def _run(coro):
    """Run an async coroutine and translate CLIError → exit code."""
    try:
        return asyncio.run(coro)
    except CLIError as e:
        fail(e.code, str(e))


async def _resolve_worker_id(ctx, name_or_id: str) -> str:
    """Resolve a worker name, short ID prefix, or full UUID to the actual ID."""
    return await resolve_id(_client(ctx), name_or_id, "/api/workers", "name", "worker")


@click.group("worker")
def worker_group() -> None:
    """Manage registered workers."""


@worker_group.command("list")
@click.pass_context
def worker_list(ctx):
    """List all registered workers."""
    async def go():
        c = _client(ctx)
        try:
            return await c.get("/api/workers")
        finally:
            await c.aclose()
    rows = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(rows)
    else:
        for r in rows:
            r["id"] = _short_id(r.get("id", ""))
        print_table(
            rows,
            columns=["id", "name", "status", "tailscale_ip", "last_heartbeat", "install_status"],
        )


@worker_group.command("show")
@click.argument("worker")
@click.pass_context
def worker_show(ctx, worker):
    """Show details for one worker (accepts name, short ID, or full UUID)."""
    worker_id = _run(_resolve_worker_id(ctx, worker))
    async def go():
        c = _client(ctx)
        try:
            return await c.get(f"/api/workers/{worker_id}")
        finally:
            await c.aclose()
    body = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        for k, v in body.items():
            click.echo(f"{k}: {v}")


@worker_group.command("add")
@click.option("--name", required=True, help="Human-readable name for the worker.")
@click.option("--tailscale-ip", default=None, help="Tailscale IP of the Pi.")
@click.option("--max-algorithms", default=2, type=int, show_default=True,
              help="Maximum concurrent algorithm instances.")
@click.pass_context
def worker_add(ctx, name, tailscale_ip, max_algorithms):
    """Register a new worker with the coordinator."""
    payload = {"name": name, "max_algorithms": max_algorithms}
    if tailscale_ip:
        payload["tailscale_ip"] = tailscale_ip

    async def go():
        c = _client(ctx)
        try:
            return await c.post("/api/workers", json=payload)
        finally:
            await c.aclose()
    body = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        click.echo(f"created worker: {_short_id(body.get('id', ''))} ({body.get('name')})")
        click.echo(f"install_token: {body.get('install_token')}")


@worker_group.command("install-command")
@click.argument("worker")
@click.pass_context
def worker_install_command(ctx, worker):
    """Print the worker install one-liner (accepts name, short ID, or full UUID)."""
    worker_id = _run(_resolve_worker_id(ctx, worker))
    import httpx as _hx
    url = resolve_coordinator_url(ctx.obj.get("coord_url"))

    async def go():
        async with _hx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{url}/api/workers/{worker_id}/install-command")
            r.raise_for_status()
            return r.text

    try:
        text = asyncio.run(go())
    except _hx.ConnectError:
        fail(3, f"coordinator unreachable at {url}")
    except _hx.HTTPStatusError as e:
        fail(2, f"HTTP {e.response.status_code}")
    click.echo(text)


@worker_group.command("regenerate-token")
@click.argument("worker")
@click.pass_context
def worker_regenerate_token(ctx, worker):
    """Issue a fresh install token for a worker (accepts name, short ID, or full UUID)."""
    worker_id = _run(_resolve_worker_id(ctx, worker))
    async def go():
        c = _client(ctx)
        try:
            return await c.post(f"/api/workers/{worker_id}/regenerate-token")
        finally:
            await c.aclose()
    body = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        click.echo(f"new install_token: {body.get('install_token')}")


@worker_group.command("update")
@click.argument("worker")
@click.option("--no-wait", is_flag=True, help="Don't wait for the worker to restart.")
@click.pass_context
def worker_update(ctx, worker, no_wait):
    """Send an update command to a worker — git pull + restart (accepts name, short ID, or full UUID)."""
    import time

    worker_id = _run(_resolve_worker_id(ctx, worker))

    async def send_update():
        c = _client(ctx)
        try:
            return await c.post(f"/api/workers/{worker_id}/update")
        finally:
            await c.aclose()

    async def get_worker():
        c = _client(ctx)
        try:
            return await c.get(f"/api/workers/{worker_id}")
        finally:
            await c.aclose()

    _run(send_update())
    click.echo("Update command sent. Worker is pulling latest code...")

    if no_wait:
        return

    # Get the current heartbeat so we can detect a fresh one after restart.
    before = _run(get_worker())
    old_heartbeat = before.get("last_heartbeat")

    # Wait for the worker to go offline (it exits after git pull).
    click.echo("  Waiting for worker to restart...", nl=False)
    went_offline = False
    for _ in range(60):  # up to 60s
        time.sleep(1)
        try:
            w = _run(get_worker())
        except SystemExit:
            # fail() calls sys.exit; worker might be unreachable briefly
            click.echo(" offline", nl=False)
            went_offline = True
            break
        if w.get("status") == "offline":
            click.echo(" offline", nl=False)
            went_offline = True
            break
        # Or: heartbeat changed (worker restarted fast without going offline)
        if w.get("last_heartbeat") != old_heartbeat and w.get("status") == "online":
            click.echo("")
            click.echo(f"  Worker restarted (new heartbeat: {w['last_heartbeat']})")
            click.echo("Update complete.")
            return
        click.echo(".", nl=False)

    if not went_offline:
        click.echo("")
        click.echo("  Timed out waiting for worker to restart. Check manually.")
        return

    # Wait for it to come back online.
    click.echo(" → waiting for online...", nl=False)
    for _ in range(60):  # up to 60s
        time.sleep(2)
        try:
            w = _run(get_worker())
        except SystemExit:
            click.echo(".", nl=False)
            continue
        if w.get("status") == "online":
            click.echo("")
            click.echo(f"  Worker back online (heartbeat: {w['last_heartbeat']})")
            click.echo("Update complete.")
            return
        click.echo(".", nl=False)

    click.echo("")
    click.echo("  Timed out waiting for worker to come back online. Check manually.")


@worker_group.command("delete")
@click.argument("worker")
@click.option("--yes", is_flag=True, help="Confirm deletion.")
@click.pass_context
def worker_delete(ctx, worker, yes):
    """Delete a worker registration (accepts name, short ID, or full UUID)."""
    if not yes:
        fail(2, "refusing to delete without --yes")
    worker_id = _run(_resolve_worker_id(ctx, worker))
    async def go():
        c = _client(ctx)
        try:
            return await c.delete(f"/api/workers/{worker_id}")
        finally:
            await c.aclose()
    _run(go())
    click.echo(f"deleted worker {worker_id}")
