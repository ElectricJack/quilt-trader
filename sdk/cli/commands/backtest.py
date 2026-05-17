"""backtest commands."""
from __future__ import annotations

import asyncio
import json as _json
import time
from datetime import datetime

import click

from sdk.cli.client import CoordinatorClient, CLIError
from sdk.cli.config import resolve_coordinator_url
from sdk.cli.output import print_json, print_table, print_status, fail


TERMINAL = {"completed", "failed", "cancelled"}


def _client(ctx) -> CoordinatorClient:
    return CoordinatorClient(resolve_coordinator_url(ctx.obj.get("coord_url")))


def _run(coro):
    try:
        return asyncio.run(coro)
    except CLIError as e:
        fail(e.code, str(e))


@click.group("backtest")
def backtest_group() -> None:
    """Run and inspect backtests."""


@backtest_group.command("run")
@click.option("--algo", "algorithm_id", required=True)
@click.option("--start", "date_start", required=True,
              help="Start date (YYYY-MM-DD).")
@click.option("--end", "date_end", required=True,
              help="End date (YYYY-MM-DD).")
@click.option("--cash", "initial_cash", default=100000.0, type=float)
@click.option("--config", "config_overrides", default=None,
              help="JSON-encoded config_overrides dict.")
@click.option("--wait", is_flag=True, default=False,
              help="Block until the run reaches a terminal state.")
@click.pass_context
def backtest_run(ctx, algorithm_id, date_start, date_end,
                  initial_cash, config_overrides, wait):
    """Launch a backtest."""
    payload = {
        "algorithm_id": algorithm_id,
        "date_range_start": _to_iso(date_start),
        "date_range_end": _to_iso(date_end),
        "initial_cash": initial_cash,
    }
    if config_overrides:
        try:
            payload["config_overrides"] = _json.loads(config_overrides)
        except Exception as e:
            fail(2, f"--config is not valid JSON: {e}")

    async def go():
        c = _client(ctx)
        try:
            return await c.post("/api/backtest-runs", json=payload)
        finally:
            await c.aclose()

    body = _run(go())
    run_id = body.get("id")
    if not run_id:
        fail(4, f"backtest create returned no id: {body}")

    if not wait:
        if ctx.obj.get("json_mode"):
            print_json(body)
        else:
            click.echo(f"backtest queued: {run_id}")
        return

    # --wait path
    last_status = None
    while True:
        async def poll():
            c = _client(ctx)
            try:
                return await c.get(f"/api/backtest-runs/{run_id}")
            finally:
                await c.aclose()
        cur = _run(poll())
        status = cur.get("status")
        if status != last_status:
            print_status(f"[{status}] {cur.get('progress_message') or ''}")
            last_status = status
        if status in TERMINAL:
            break
        time.sleep(2)

    if status != "completed":
        fail(4, f"backtest {status}: {cur.get('error_message') or ''}")

    # Print a small summary
    if ctx.obj.get("json_mode"):
        print_json(cur)
    else:
        click.echo(f"backtest completed: {run_id}")
        for k in ("total_return", "sharpe_ratio", "max_drawdown",
                  "trade_count", "win_rate"):
            click.echo(f"  {k}: {cur.get(k)}")


@backtest_group.command("list")
@click.option("--algo", "algorithm_id", default=None)
@click.pass_context
def backtest_list(ctx, algorithm_id):
    """List backtest runs."""
    params = {"algorithm_id": algorithm_id} if algorithm_id else None
    async def go():
        c = _client(ctx)
        try:
            return await c.get("/api/backtest-runs", params=params)
        finally:
            await c.aclose()
    rows = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(rows)
    else:
        print_table(rows, columns=["id", "algorithm_id", "status",
                                    "date_range_start", "date_range_end",
                                    "total_return", "sharpe_ratio"])


@backtest_group.command("show")
@click.argument("run_id")
@click.pass_context
def backtest_show(ctx, run_id):
    """Show a backtest run summary."""
    async def go():
        c = _client(ctx)
        try:
            return await c.get(f"/api/backtest-runs/{run_id}")
        finally:
            await c.aclose()
    body = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        for k, v in body.items():
            click.echo(f"{k}: {v}")


@backtest_group.command("report")
@click.argument("run_id")
@click.pass_context
def backtest_report(ctx, run_id):
    """Fetch the full report blob (intended with --json)."""
    async def go():
        c = _client(ctx)
        try:
            return await c.get(f"/api/backtest-runs/{run_id}/report")
        finally:
            await c.aclose()
    body = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        km = (body.get("key_metrics") or {}).get("strategy") or {}
        click.echo("KPIs:")
        for k in ("cagr", "total_return", "sharpe_ratio", "sortino_ratio",
                  "max_drawdown", "calmar_ratio"):
            click.echo(f"  {k}: {km.get(k)}")


@backtest_group.command("trades")
@click.argument("run_id")
@click.option("-n", "limit", default=100, type=int)
@click.pass_context
def backtest_trades(ctx, run_id, limit):
    """List trades for a backtest run."""
    params = {"limit": limit}
    async def go():
        c = _client(ctx)
        try:
            return await c.get(f"/api/backtest-runs/{run_id}/trades", params=params)
        finally:
            await c.aclose()
    body = _run(go())
    items = body.get("items", []) if isinstance(body, dict) else body
    if ctx.obj.get("json_mode"):
        print_json(items)
    else:
        print_table(items, columns=["timestamp", "symbol", "side",
                                     "quantity", "fill_price"])


@backtest_group.command("delete")
@click.argument("run_id")
@click.option("--yes", is_flag=True)
@click.pass_context
def backtest_delete(ctx, run_id, yes):
    """Delete a backtest run."""
    if not yes:
        fail(2, "refusing to delete without --yes")
    async def go():
        c = _client(ctx)
        try:
            return await c.delete(f"/api/backtest-runs/{run_id}")
        finally:
            await c.aclose()
    _run(go())
    click.echo(f"deleted {run_id}")


def _to_iso(date_str: str) -> str:
    """Accept YYYY-MM-DD; emit an ISO datetime at midnight UTC for the API."""
    try:
        # Accept both YYYY-MM-DD and full ISO
        if "T" in date_str:
            return date_str
        return f"{date_str}T00:00:00+00:00"
    except Exception:
        fail(2, f"invalid date: {date_str}")
