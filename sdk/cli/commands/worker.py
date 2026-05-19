"""worker commands."""
from __future__ import annotations

import asyncio

import click

from sdk.cli.client import CoordinatorClient, CLIError
from sdk.cli.config import resolve_coordinator_url
from sdk.cli.output import print_json, print_table, fail


def _client(ctx) -> CoordinatorClient:
    return CoordinatorClient(resolve_coordinator_url(ctx.obj.get("coord_url")))


def _run(coro):
    """Run an async coroutine and translate CLIError → exit code."""
    try:
        return asyncio.run(coro)
    except CLIError as e:
        fail(e.code, str(e))


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
        print_table(
            rows,
            columns=["id", "name", "status", "tailscale_ip", "last_heartbeat", "install_status"],
        )


@worker_group.command("show")
@click.argument("worker_id")
@click.pass_context
def worker_show(ctx, worker_id):
    """Show details for one worker."""
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
        click.echo(f"created worker: {body.get('id')} ({body.get('name')})")
        click.echo(f"install_token: {body.get('install_token')}")


@worker_group.command("install-command")
@click.argument("worker_id")
@click.pass_context
def worker_install_command(ctx, worker_id):
    """Print the worker install one-liner."""
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
@click.argument("worker_id")
@click.pass_context
def worker_regenerate_token(ctx, worker_id):
    """Issue a fresh install token for a worker (resets status to pending)."""
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
@click.argument("worker_id")
@click.pass_context
def worker_update(ctx, worker_id):
    """Send an update command to a worker (git pull + restart)."""
    async def go():
        c = _client(ctx)
        try:
            return await c.post(f"/api/workers/{worker_id}/update")
        finally:
            await c.aclose()
    body = _run(go())
    click.echo(f"Update command sent to worker {worker_id}. The worker will pull latest code and restart.")


@worker_group.command("delete")
@click.argument("worker_id")
@click.option("--yes", is_flag=True, help="Confirm deletion.")
@click.pass_context
def worker_delete(ctx, worker_id, yes):
    """Delete a worker registration."""
    if not yes:
        fail(2, "refusing to delete without --yes")

    async def go():
        c = _client(ctx)
        try:
            return await c.delete(f"/api/workers/{worker_id}")
        finally:
            await c.aclose()
    _run(go())
    click.echo(f"deleted worker {worker_id}")
