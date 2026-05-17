"""deployment (deploy) commands."""
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


@click.group("deployment")
def deployment_group() -> None:
    """Manage algorithm deployments."""


@deployment_group.command("list")
@click.option("--algo", "algorithm_id", default=None)
@click.option("--worker", "worker_id", default=None)
@click.option("--account", "account_id", default=None)
@click.option("--status", default=None)
@click.pass_context
def deployment_list(ctx, algorithm_id, worker_id, account_id, status):
    """List deployments."""
    params = {}
    if algorithm_id: params["algorithm_id"] = algorithm_id
    if worker_id: params["worker_id"] = worker_id
    if account_id: params["account_id"] = account_id
    async def go():
        c = _client(ctx)
        try:
            return await c.get("/api/deployments", params=params)
        finally:
            await c.aclose()
    rows = _run(go())
    if status:
        rows = [r for r in rows if r.get("status") == status]
    if ctx.obj.get("json_mode"):
        print_json(rows)
    else:
        print_table(rows, columns=["id", "algorithm_name", "account_name",
                                    "worker_name", "status", "active_run_id"])


@deployment_group.command("show")
@click.argument("deployment_id")
@click.pass_context
def deployment_show(ctx, deployment_id):
    """Show one deployment."""
    async def go():
        c = _client(ctx)
        try:
            return await c.get(f"/api/deployments/{deployment_id}")
        finally:
            await c.aclose()
    body = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        for k, v in body.items():
            click.echo(f"{k}: {v}")


@deployment_group.command("create")
@click.option("--algo", "algorithm_id", required=True)
@click.option("--account", "account_id", required=True)
@click.option("--worker", "worker_id", required=True)
@click.option("--config", "config_values", default=None,
              help="JSON-encoded config_values dict.")
@click.pass_context
def deployment_create(ctx, algorithm_id, account_id, worker_id, config_values):
    """Create a new deployment."""
    payload = {"account_id": account_id, "worker_id": worker_id}
    if config_values:
        try:
            payload["config_values"] = _json.loads(config_values)
        except Exception as e:
            fail(2, f"--config is not valid JSON: {e}")
    async def go():
        c = _client(ctx)
        try:
            return await c.post(f"/api/algorithms/{algorithm_id}/instances", json=payload)
        finally:
            await c.aclose()
    body = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        click.echo(f"created: {body.get('id')}")


@deployment_group.command("start")
@click.argument("deployment_id")
@click.pass_context
def deployment_start(ctx, deployment_id):
    """Start a deployment."""
    async def go():
        c = _client(ctx)
        try:
            return await c.post(f"/api/deployments/{deployment_id}/start")
        finally:
            await c.aclose()
    body = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        click.echo(f"started, active_run_id={body.get('active_run_id')}")


@deployment_group.command("stop")
@click.argument("deployment_id")
@click.pass_context
def deployment_stop(ctx, deployment_id):
    """Stop a deployment."""
    async def go():
        c = _client(ctx)
        try:
            return await c.post(f"/api/deployments/{deployment_id}/stop")
        finally:
            await c.aclose()
    _run(go())
    click.echo("stopped")


@deployment_group.command("delete")
@click.argument("deployment_id")
@click.option("--yes", is_flag=True)
@click.pass_context
def deployment_delete(ctx, deployment_id, yes):
    """Delete a deployment."""
    if not yes:
        fail(2, "refusing to delete without --yes")
    async def go():
        c = _client(ctx)
        try:
            return await c.delete(f"/api/deployments/{deployment_id}")
        finally:
            await c.aclose()
    _run(go())
    click.echo(f"deleted {deployment_id}")


@deployment_group.command("runs")
@click.argument("deployment_id")
@click.pass_context
def deployment_runs(ctx, deployment_id):
    """List runs for a deployment."""
    async def go():
        c = _client(ctx)
        try:
            return await c.get(f"/api/deployments/{deployment_id}/runs")
        finally:
            await c.aclose()
    rows = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(rows)
    else:
        print_table(rows, columns=["run_number", "status", "started_at",
                                    "stopped_at", "net_pnl", "trade_count"])


@deployment_group.command("report")
@click.argument("deployment_id")
@click.option("--run", "run_id", default=None)
@click.pass_context
def deployment_report(ctx, deployment_id, run_id):
    """Show the deployment report (KPIs / equity curve)."""
    params = {"run_id": run_id} if run_id else {}
    async def go():
        c = _client(ctx)
        try:
            return await c.get(f"/api/deployments/{deployment_id}/report", params=params)
        finally:
            await c.aclose()
    body = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        km = (body.get("key_metrics") or {}).get("strategy") or {}
        click.echo(f"deployment: {body.get('deployment_id')}")
        click.echo(f"generated_at: {body.get('generated_at')}")
        click.echo(f"\nKPIs:")
        for k in ("cagr", "total_return", "sharpe_ratio", "sortino_ratio",
                  "max_drawdown", "calmar_ratio", "trade_count", "win_rate"):
            click.echo(f"  {k}: {km.get(k)}")


@deployment_group.command("trades")
@click.argument("deployment_id")
@click.option("-n", "limit", default=100, type=int)
@click.option("--run", "run_id", default=None)
@click.pass_context
def deployment_trades(ctx, deployment_id, limit, run_id):
    """List trades for a deployment."""
    params = {"limit": limit}
    if run_id:
        params["run_id"] = run_id
    async def go():
        c = _client(ctx)
        try:
            return await c.get(f"/api/deployments/{deployment_id}/trades", params=params)
        finally:
            await c.aclose()
    body = _run(go())
    items = body.get("items", []) if isinstance(body, dict) else body
    if ctx.obj.get("json_mode"):
        print_json(items)
    else:
        print_table(items, columns=["timestamp", "symbol", "side",
                                     "quantity", "fill_price"])


@deployment_group.command("activity")
@click.argument("deployment_id")
@click.option("--follow", "-f", is_flag=True, default=False)
@click.option("--severity", default="info")
@click.option("--kind", default="all", type=click.Choice(["all", "event", "log"]))
@click.option("-n", "limit", default=100, type=int)
@click.option("--no-history", is_flag=True, default=False)
@click.pass_context
def deployment_activity(ctx, deployment_id, follow, severity, kind, limit, no_history):
    """Show or follow activity events for a deployment."""
    coord_url = resolve_coordinator_url(ctx.obj.get("coord_url"))

    if follow:
        from sdk.cli.follow import follow_target
        ws_url = (coord_url.replace("http://", "ws://")
                            .replace("https://", "wss://")
                            + "/ws/dashboard")
        history_url = (None if no_history
                        else f"{coord_url}/api/deployments/{deployment_id}/activity"
                              f"?limit=50&severity={severity}&kind={kind}")
        code = asyncio.run(follow_target(
            ws_url=ws_url,
            target=f"deployment:{deployment_id}",
            severity_floor=severity,
            kind=kind,
            json_mode=ctx.obj.get("json_mode", False),
            print_history=not no_history,
            history_url=history_url,
        ))
        raise SystemExit(code)

    # Non-follow path (unchanged from C5.1)
    params = {"limit": limit, "severity": severity, "kind": kind}
    async def go():
        c = _client(ctx)
        try:
            return await c.get(f"/api/deployments/{deployment_id}/activity", params=params)
        finally:
            await c.aclose()
    body = _run(go())
    items = body.get("items", []) if isinstance(body, dict) else body
    if ctx.obj.get("json_mode"):
        print_json(items)
    else:
        print_table(items, columns=["timestamp", "severity", "kind",
                                     "event_type", "logger_name", "message"])
