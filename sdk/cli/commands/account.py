"""account commands."""
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


@click.group("account")
def account_group() -> None:
    """Manage broker accounts."""


@account_group.command("list")
@click.pass_context
def account_list(ctx):
    """List all configured accounts."""
    async def go():
        c = _client(ctx)
        try:
            return await c.get("/api/accounts")
        finally:
            await c.aclose()
    rows = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(rows)
    else:
        print_table(
            rows,
            columns=["id", "name", "broker_type", "environment", "options_level", "locked_by"],
        )


@account_group.command("show")
@click.argument("account_id")
@click.pass_context
def account_show(ctx, account_id):
    """Show details for one account."""
    async def go():
        c = _client(ctx)
        try:
            return await c.get(f"/api/accounts/{account_id}")
        finally:
            await c.aclose()
    body = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        for k, v in body.items():
            click.echo(f"{k}: {v}")


@account_group.command("create")
@click.option("--name", required=True, help="Display name for the account.")
@click.option("--broker", "broker_type", required=True,
              help="Broker identifier (e.g. alpaca, tradier).")
@click.option("--env", "environment", default="paper", show_default=True,
              help="'paper' or 'live'.")
@click.option("--asset-types", "asset_types", default="equities",
              help="Comma-separated list of supported asset types (e.g. equities,options).")
@click.option("--options-level", default=None, type=int,
              help="Options trading level (1-4).")
@click.option("--api-key", default=None, help="API key (Alpaca / most brokers).")
@click.option("--secret-key", default=None, help="Secret key (Alpaca).")
@click.option("--access-token", default=None, help="Access token (Tradier).")
@click.option("--account-id", "account_id_cred", default=None,
              help="Broker account ID (Tradier).")
@click.pass_context
def account_create(
    ctx, name, broker_type, environment, asset_types,
    options_level, api_key, secret_key, access_token, account_id_cred,
):
    """Create a new broker account."""
    credentials: dict = {}
    if api_key:
        credentials["api_key"] = api_key
    if secret_key:
        credentials["secret_key"] = secret_key
    if access_token:
        credentials["access_token"] = access_token
    if account_id_cred:
        credentials["account_id"] = account_id_cred

    supported_asset_types = [t.strip() for t in asset_types.split(",") if t.strip()]

    payload = {
        "name": name,
        "broker_type": broker_type,
        "environment": environment,
        "credentials": credentials,
        "supported_asset_types": supported_asset_types,
    }
    if options_level is not None:
        payload["options_level"] = options_level

    async def go():
        c = _client(ctx)
        try:
            return await c.post("/api/accounts", json=payload)
        finally:
            await c.aclose()
    body = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        click.echo(f"created account: {body.get('id')} ({body.get('name')})")


@account_group.command("update")
@click.argument("account_id")
@click.option("--name", default=None, help="New display name.")
@click.option("--api-key", default=None, help="Updated API key.")
@click.option("--secret-key", default=None, help="Updated secret key.")
@click.option("--access-token", default=None, help="Updated access token.")
@click.pass_context
def account_update(ctx, account_id, name, api_key, secret_key, access_token):
    """Update an existing account's name or credentials."""
    payload: dict = {}
    if name:
        payload["name"] = name
    credentials: dict = {}
    if api_key:
        credentials["api_key"] = api_key
    if secret_key:
        credentials["secret_key"] = secret_key
    if access_token:
        credentials["access_token"] = access_token
    if credentials:
        payload["credentials"] = credentials

    if not payload:
        fail(2, "nothing to update — supply --name, --api-key, --secret-key, or --access-token")

    async def go():
        c = _client(ctx)
        try:
            return await c.patch(f"/api/accounts/{account_id}", json=payload)
        finally:
            await c.aclose()
    body = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        click.echo(f"updated account {account_id}")


@account_group.command("unlock")
@click.argument("account_id")
@click.pass_context
def account_unlock(ctx, account_id):
    """Clear the locked_by field on an account (force-release from a stopped instance)."""
    # The accounts API does not expose a dedicated /unlock endpoint.
    # PATCH with locked_by is not in the AccountUpdate schema either — the backend
    # clears locked_by automatically when an instance stops. As a workaround, this
    # command is noted as a limitation: use the coordinator dashboard or directly
    # patch the DB if an account is stuck locked.
    fail(
        2,
        "account unlock is not supported via the API: the coordinator clears locked_by "
        "automatically when instances stop. If the account is stuck, restart the coordinator "
        "or use the dashboard.",
    )


@account_group.command("delete")
@click.argument("account_id")
@click.option("--yes", is_flag=True, help="Confirm deletion.")
@click.pass_context
def account_delete(ctx, account_id, yes):
    """Delete an account and all its associated data."""
    if not yes:
        fail(2, "refusing to delete without --yes")

    async def go():
        c = _client(ctx)
        try:
            return await c.delete(f"/api/accounts/{account_id}")
        finally:
            await c.aclose()
    _run(go())
    click.echo(f"deleted account {account_id}")
