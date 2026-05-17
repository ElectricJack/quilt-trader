"""algorithm commands."""
from __future__ import annotations

import asyncio
from pathlib import Path

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


@click.group("algorithm")
def algorithm_group() -> None:
    """Manage installed algorithms."""


@algorithm_group.command("list")
@click.pass_context
def algorithm_list(ctx):
    """List installed algorithms."""
    async def go():
        c = _client(ctx)
        try:
            return await c.get("/api/algorithms")
        finally:
            await c.aclose()
    rows = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(rows)
    else:
        print_table(rows, columns=["id", "name", "version", "commit_hash", "install_status"])


@algorithm_group.command("show")
@click.argument("algorithm_id")
@click.pass_context
def algorithm_show(ctx, algorithm_id):
    """Show one algorithm."""
    async def go():
        c = _client(ctx)
        try:
            return await c.get(f"/api/algorithms/{algorithm_id}")
        finally:
            await c.aclose()
    body = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        for k, v in body.items():
            click.echo(f"{k}: {v}")


@algorithm_group.command("install")
@click.argument("source")
@click.option("--as", "name_override", default=None, help="Override the algorithm name.")
@click.option("--ref", default=None, help="Branch or commit SHA (GitHub installs only).")
@click.pass_context
def algorithm_install(ctx, source, name_override, ref):
    """Install from a GitHub URL or a local directory path."""
    # Resolve relative local paths to absolute before sending
    if not (source.startswith("http://") or source.startswith("https://")):
        p = Path(source)
        if p.exists():
            source = str(p.resolve())
    payload = {"source": source}
    if name_override:
        payload["name_override"] = name_override
    if ref:
        payload["ref"] = ref

    async def go():
        c = _client(ctx)
        try:
            return await c.post("/api/algorithms/install", json=payload)
        finally:
            await c.aclose()
    body = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        click.echo(f"installed: {body.get('id')} ({body.get('name')})")


@algorithm_group.command("uninstall")
@click.argument("algorithm_id")
@click.option("--yes", is_flag=True, help="Confirm deletion.")
@click.pass_context
def algorithm_uninstall(ctx, algorithm_id, yes):
    """Uninstall an algorithm."""
    if not yes:
        fail(2, "refusing to uninstall without --yes")

    async def go():
        c = _client(ctx)
        try:
            return await c.delete(f"/api/algorithms/{algorithm_id}")
        finally:
            await c.aclose()
    _run(go())
    click.echo(f"uninstalled {algorithm_id}")


@algorithm_group.command("update")
@click.argument("algorithm_id")
@click.pass_context
def algorithm_update(ctx, algorithm_id):
    """Pull the latest commit for an installed GitHub algorithm."""
    async def go():
        c = _client(ctx)
        try:
            return await c.post(f"/api/algorithms/{algorithm_id}/update")
        finally:
            await c.aclose()
    body = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        click.echo(f"updated: {body.get('id')} ({body.get('name')}) → {body.get('commit_hash')}")
