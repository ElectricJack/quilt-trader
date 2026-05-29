"""settings commands.

The coordinator settings API exposes key-specific endpoints rather than a
generic /api/settings/{key} CRUD surface. Supported keys and their slugs:

  github_pat          → /api/settings/github-pat
  discord_bot_token   → /api/settings/discord-token
  polygon_api_key     → /api/settings/polygon-key
  fmp_api_key         → /api/settings/fmp-key
  coordinator_ip      → /api/settings/coordinator-ip
  tailscale_authkey   → /api/settings/tailscale-authkey
  theta_data          → /api/settings/theta-data   (uses --username + --password)

GET /api/settings returns a status dict (not individual values — secrets are
stored encrypted and the API only reveals whether they are set).
"""
from __future__ import annotations

import asyncio

import click

from sdk.cli.client import CoordinatorClient, CLIError
from sdk.cli.config import resolve_coordinator_url
from sdk.cli.output import print_json, print_table, fail


# Mapping from user-friendly key name → API slug
_KEY_TO_SLUG: dict[str, str] = {
    "github_pat": "github-pat",
    "github-pat": "github-pat",
    "discord_bot_token": "discord-token",
    "discord-token": "discord-token",
    "polygon_api_key": "polygon-key",
    "polygon-key": "polygon-key",
    "fmp_api_key": "fmp-key",
    "fmp-key": "fmp-key",
    "coordinator_ip": "coordinator-ip",
    "coordinator-ip": "coordinator-ip",
    "tailscale_authkey": "tailscale-authkey",
    "tailscale-authkey": "tailscale-authkey",
}

_KNOWN_KEYS = sorted({k for k in _KEY_TO_SLUG if not k.startswith("github")
                      or True}, key=str)


def _client(ctx) -> CoordinatorClient:
    return CoordinatorClient(resolve_coordinator_url(ctx.obj.get("coord_url")))


def _run(coro):
    try:
        return asyncio.run(coro)
    except CLIError as e:
        fail(e.code, str(e))


@click.group("settings")
def settings_group() -> None:
    """Manage coordinator settings."""


@settings_group.command("list")
@click.pass_context
def settings_list(ctx):
    """Show all settings (alias for `settings get`)."""
    ctx.invoke(settings_get)


@settings_group.command("get")
@click.pass_context
def settings_get(ctx):
    """Show the current settings status (which secrets are configured)."""
    async def go():
        c = _client(ctx)
        try:
            return await c.get("/api/settings")
        finally:
            await c.aclose()
    body = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        rows = [{"key": k, "value": str(v)} for k, v in sorted(body.items())]
        print_table(rows, columns=["key", "value"])


@settings_group.command("set")
@click.argument("key")
@click.argument("value", required=False, default=None)
@click.option("--username", default=None, help="Username (theta-data only).")
@click.option("--password", default=None, help="Password (theta-data only).")
@click.pass_context
def settings_set(ctx, key, value, username, password):
    """Set a setting value.

    For theta_data / theta-data, use --username and --password instead of VALUE.

    Supported keys: github-pat, discord-token, polygon-key, coordinator-ip,
    tailscale-authkey, theta-data.
    """
    # Normalise key
    normalised = key.lower().replace("_", "-")

    if normalised == "theta-data":
        if not username or not password:
            fail(2, "theta-data requires --username and --password")

        async def go_theta():
            c = _client(ctx)
            try:
                return await c.put("/api/settings/theta-data",
                                   json={"username": username, "password": password})
            finally:
                await c.aclose()

        # CoordinatorClient doesn't have put(); use post via httpx directly.
        async def go_theta_put():
            import httpx as _hx
            url = resolve_coordinator_url(ctx.obj.get("coord_url"))
            try:
                async with _hx.AsyncClient(timeout=10.0) as c:
                    r = await c.put(
                        f"{url}/api/settings/theta-data",
                        json={"username": username, "password": password},
                    )
                    r.raise_for_status()
                    return r.json()
            except _hx.ConnectError:
                fail(3, f"coordinator unreachable at {url}")
            except _hx.HTTPStatusError as e:
                fail(2, f"HTTP {e.response.status_code}")

        body = _run(go_theta_put())
        if ctx.obj.get("json_mode"):
            print_json(body)
        else:
            click.echo("theta-data credentials updated")
        return

    slug = _KEY_TO_SLUG.get(normalised)
    if not slug:
        fail(2, f"unknown key {key!r}. Known keys: {', '.join(sorted(set(_KEY_TO_SLUG.values())))}")

    if value is None:
        fail(2, f"VALUE is required for key {key!r}")

    async def go_put():
        import httpx as _hx
        url = resolve_coordinator_url(ctx.obj.get("coord_url"))
        try:
            async with _hx.AsyncClient(timeout=10.0) as c:
                r = await c.put(
                    f"{url}/api/settings/{slug}",
                    json={"value": value},
                )
                r.raise_for_status()
                return r.json()
        except _hx.ConnectError:
            fail(3, f"coordinator unreachable at {url}")
        except _hx.HTTPStatusError as e:
            fail(2, f"HTTP {e.response.status_code}")

    body = _run(go_put())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        click.echo(f"set {key}")


@settings_group.command("unset")
@click.argument("key")
@click.pass_context
def settings_unset(ctx, key):
    """Delete / clear a setting.

    Supported keys: github-pat, discord-token, polygon-key, coordinator-ip,
    tailscale-authkey, theta-data.
    """
    normalised = key.lower().replace("_", "-")

    # theta-data has its own delete slug
    if normalised == "theta-data":
        slug = "theta-data"
    else:
        slug = _KEY_TO_SLUG.get(normalised)
        if not slug:
            fail(2, f"unknown key {key!r}. Known keys: {', '.join(sorted(set(_KEY_TO_SLUG.values())))}")

    async def go():
        c = _client(ctx)
        try:
            return await c.delete(f"/api/settings/{slug}")
        finally:
            await c.aclose()

    body = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        click.echo(f"unset {key}")
