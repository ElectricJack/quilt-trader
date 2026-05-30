"""data commands: subscriptions, downloads, scrapers."""
from __future__ import annotations

import asyncio
import json as _json

import click

from sdk.cli.client import CoordinatorClient, CLIError
from sdk.cli.config import resolve_coordinator_url
from sdk.cli.output import print_json, print_table, fail


def _client(ctx) -> CoordinatorClient:
    return CoordinatorClient(resolve_coordinator_url(ctx.obj.get("coord_url")))


def _run(coro):
    try:
        return asyncio.run(coro)
    except CLIError as e:
        fail(e.code, str(e))


@click.group("data")
def data_group() -> None:
    """Control market data subscriptions, downloads, and scrapers."""


# ── Live subscriptions ───────────────────────────────────────────────

@data_group.command("subscribe")
@click.argument("broker")
@click.argument("symbol")
@click.option("--retention-hours", default=24, type=int)
@click.pass_context
def data_subscribe(ctx, broker, symbol, retention_hours):
    """Start streaming live market data for (broker, symbol)."""
    payload = {"broker": broker, "symbol": symbol,
               "tick_retention_hours": retention_hours}
    async def go():
        c = _client(ctx)
        try:
            return await c.post("/api/live-subscriptions", json=payload)
        finally:
            await c.aclose()
    body = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        click.echo(f"subscribed: {body.get('id')} ({broker}/{symbol})")


@data_group.command("unsubscribe")
@click.argument("broker")
@click.argument("symbol")
@click.pass_context
def data_unsubscribe(ctx, broker, symbol):
    """Stop streaming live market data for (broker, symbol)."""
    # Need to find the subscription id first
    async def find():
        c = _client(ctx)
        try:
            return await c.get("/api/live-subscriptions")
        finally:
            await c.aclose()
    rows = _run(find())
    match = next((r for r in rows
                  if r.get("broker") == broker and r.get("symbol") == symbol), None)
    if match is None:
        fail(2, f"no subscription for {broker}/{symbol}")
    sub_id = match["id"]
    async def go():
        c = _client(ctx)
        try:
            return await c.post(f"/api/live-subscriptions/{sub_id}/unsubscribe")
        finally:
            await c.aclose()
    _run(go())
    click.echo(f"unsubscribed {broker}/{symbol}")


@data_group.command("subscriptions")
@click.pass_context
def data_subscriptions(ctx):
    """List live data subscriptions."""
    async def go():
        c = _client(ctx)
        try:
            return await c.get("/api/live-subscriptions")
        finally:
            await c.aclose()
    rows = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(rows)
    else:
        print_table(rows, columns=["id", "broker", "symbol", "status",
                                    "tick_rate_per_min", "last_tick_at",
                                    "dependent_count"])


# ── Downloads ────────────────────────────────────────────────────────

@data_group.command("download")
@click.option("--symbol", required=True, multiple=True,
              help="Symbol to download (repeat for multiple).")
@click.option("--start", "date_start", required=True)
@click.option("--end", "date_end", required=True)
@click.option("--provider", default="polygon")
@click.option("--timeframe", default="1day")
@click.option("--data-type", default="bars")
@click.pass_context
def data_download(ctx, symbol, date_start, date_end, provider, timeframe, data_type):
    """Create a historical market data download job."""
    payload = {
        "symbols": list(symbol),
        "date_range_start": _to_iso(date_start),
        "date_range_end": _to_iso(date_end),
        "provider": provider,
        "timeframe": timeframe,
        "data_type": data_type,
    }
    async def go():
        c = _client(ctx)
        try:
            return await c.post("/api/data/downloads", json=payload)
        finally:
            await c.aclose()
    body = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        click.echo(f"download queued: {body.get('id')}")


@data_group.command("downloads")
@click.pass_context
def data_downloads(ctx):
    """List download jobs."""
    async def go():
        c = _client(ctx)
        try:
            return await c.get("/api/data/downloads")
        finally:
            await c.aclose()
    rows = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(rows)
    else:
        print_table(rows, columns=["id", "provider", "status",
                                    "progress_current", "progress_total",
                                    "date_range_start", "date_range_end"])


@data_group.command("available")
@click.pass_context
def data_available(ctx):
    """Show locally cached market data."""
    async def go():
        c = _client(ctx)
        try:
            return await c.get("/api/data/available")
        finally:
            await c.aclose()
    body = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        # Response shape varies; just pretty-print
        click.echo(_json.dumps(body, indent=2, default=str))


# ── Scrapers ─────────────────────────────────────────────────────────

@data_group.command("scrapers")
@click.pass_context
def data_scrapers(ctx):
    """List installed scrapers."""
    async def go():
        c = _client(ctx)
        try:
            return await c.get("/api/scrapers")
        finally:
            await c.aclose()
    rows = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(rows)
    else:
        print_table(rows, columns=["name", "version", "status",
                                    "schedule", "last_success", "dependent_algorithm_count"])


@data_group.command("scraper-run")
@click.argument("name")
@click.pass_context
def data_scraper_run(ctx, name):
    """Trigger a scraper run."""
    async def go():
        c = _client(ctx)
        try:
            return await c.post(f"/api/scrapers/{name}/run")
        finally:
            await c.aclose()
    body = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        click.echo(f"scraper run triggered: {name}")


def _to_iso(date_str: str) -> str:
    if "T" in date_str:
        return date_str
    return f"{date_str}T00:00:00+00:00"


# ── Datasets ─────────────────────────────────────────────────────────

@click.group("datasets")
def datasets_group() -> None:
    """Manage time-series datasets (FMP and beyond)."""


data_group.add_command(datasets_group)


@datasets_group.command("list")
@click.pass_context
def datasets_list(ctx):
    """List available datasets."""
    async def go():
        c = _client(ctx)
        try:
            return await c.get("/api/datasets")
        finally:
            await c.aclose()
    rows = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(rows)
    else:
        for r in rows:
            click.echo(
                f"{r['name']:<40} provider={r['provider']}"
                f" pagination={r['pagination']}"
            )


@datasets_group.command("show")
@click.argument("name")
@click.pass_context
def datasets_show(ctx, name):
    """Show details for a single dataset."""
    async def go():
        c = _client(ctx)
        try:
            return await c.get(f"/api/datasets/{name}")
        finally:
            await c.aclose()
    spec = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(spec)
    else:
        click.echo(_json.dumps(spec, indent=2, default=str))


@datasets_group.command("download")
@click.argument("name")
@click.option("--symbol", default=None, help="Filter by symbol.")
@click.option("--from", "date_from", default=None, help="Start date (YYYY-MM-DD).")
@click.option("--to", "date_to", default=None, help="End date (YYYY-MM-DD).")
@click.option("--param", "extra_params", multiple=True,
              metavar="KEY=VALUE", help="Extra dataset params (repeat for multiple).")
@click.pass_context
def datasets_download(ctx, name, symbol, date_from, date_to, extra_params):
    """Enqueue a dataset download job."""
    params: dict = {}
    if symbol:
        params["symbol"] = symbol
    if date_from:
        params["from"] = date_from
    if date_to:
        params["to"] = date_to
    for kv in extra_params:
        k, _, v = kv.partition("=")
        params[k] = v
    payload = {"name": name, "params": params}
    async def go():
        c = _client(ctx)
        try:
            return await c.post("/api/datasets/downloads", json=payload)
        finally:
            await c.aclose()
    body = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        click.echo(
            f"queued download #{body.get('id')} for {name}"
            f" (status={body.get('status')})"
        )


@datasets_group.command("downloads")
@click.option("--status", default=None, help="Filter by status.")
@click.option("--provider", default=None, help="Filter by provider.")
@click.pass_context
def datasets_downloads(ctx, status, provider):
    """List dataset download jobs."""
    qs_parts = {}
    if status:
        qs_parts["status"] = status
    if provider:
        qs_parts["provider"] = provider
    async def go():
        c = _client(ctx)
        try:
            return await c.get("/api/datasets/downloads",
                               params=qs_parts if qs_parts else None)
        finally:
            await c.aclose()
    rows = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(rows)
    else:
        for r in rows:
            pct = r.get("progress_pct", 0) or 0
            click.echo(
                f"#{r['id']:<5} {r.get('dataset_name', ''):<32}"
                f" {r.get('status', ''):<14}"
                f" rows={r.get('rows_fetched', 0)}"
                f" progress={pct * 100:.0f}%"
            )


@datasets_group.command("quota")
@click.pass_context
def datasets_quota(ctx):
    """Show API quota usage for dataset providers."""
    async def go():
        c = _client(ctx)
        try:
            return await c.get("/api/datasets/quota")
        finally:
            await c.aclose()
    rows = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(rows)
    elif not rows:
        click.echo("(no usage yet today)")
    else:
        for r in rows:
            flag = " EXHAUSTED" if r.get("exhausted") else ""
            click.echo(
                f"{r['provider']:<10}"
                f" {r['calls_used']}/{r['daily_limit']}{flag}"
            )
