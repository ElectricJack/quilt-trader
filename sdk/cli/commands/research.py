"""Research CLI — thin client against /api/research/* endpoints."""
from __future__ import annotations

import json
from pathlib import Path

import click

from sdk.cli.client import CLIError, CoordinatorClient
from sdk.cli.config import resolve_coordinator_url
from sdk.cli.output import fail, print_json


def _client(ctx) -> CoordinatorClient:
    return CoordinatorClient(resolve_coordinator_url(ctx.obj.get("coord_url")))


def _run(coro):
    import asyncio
    try:
        return asyncio.run(coro)
    except CLIError as e:
        fail(e.code, str(e))


@click.group("research")
def research_group() -> None:
    """Strategy Validation Lab — sessions, sweeps, walk-forward, reports."""


@research_group.group("session")
def session_group() -> None:
    """Manage OptimizationSession records."""


@session_group.command("create")
@click.option("--name", required=True, help="Unique session name.")
@click.option("--hypothesis", required=True, help="Pre-registered hypothesis text.")
@click.option(
    "--parameter-space",
    required=True,
    help='JSON parameter space, e.g., \'{"vol_target": [0.10, 0.15]}\'. May also be a path to a JSON or YAML file.',
)
@click.option(
    "--criteria",
    required=True,
    help='JSON pre-registered criteria, e.g., \'{"oos_sharpe_lci": 0.5}\'.',
)
@click.option("--notes", default="", help="Free-form notes.")
@click.pass_context
def session_create(ctx, name, hypothesis, parameter_space, criteria, notes):
    """Create a new OptimizationSession (pre-registration step)."""
    payload = {
        "name": name,
        "hypothesis": hypothesis,
        "parameter_space": _parse_json_or_yaml_or_file(parameter_space),
        "pre_registered_criteria": _parse_json_or_yaml_or_file(criteria),
        "notes": notes,
    }
    async def go():
        c = _client(ctx)
        try:
            return await c.post("/api/research/sessions", json=payload)
        finally:
            await c.aclose()
    body = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        click.echo(f"Created OptimizationSession id={body['id']} name={body['name']}")


@session_group.command("list")
@click.pass_context
def session_list(ctx):
    """List all OptimizationSessions."""
    async def go():
        c = _client(ctx)
        try:
            return await c.get("/api/research/sessions")
        finally:
            await c.aclose()
    rows = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(rows)
    else:
        for r in rows:
            click.echo(f"{r['id']}\t{r['name']}\t{r['status']}\truns={r['n_runs']}\t{r['created_at']}")


@session_group.command("show")
@click.argument("session_id", type=int)
@click.pass_context
def session_show(ctx, session_id):
    """Show details of one OptimizationSession."""
    async def go():
        c = _client(ctx)
        try:
            return await c.get(f"/api/research/sessions/{session_id}")
        finally:
            await c.aclose()
    body = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        click.echo(json.dumps(body, indent=2))


@research_group.command("sweep")
@click.option("--session-id", type=int, required=True)
@click.option("--manifest", required=True, help="Path to the strategy's quilt.yaml (server-resolvable).")
@click.option("--base-config", required=True, help="Path to JSON file with base BacktestConfig (or inline JSON).")
@click.option("--parameter-space", default=None, help='Optional override of the session\'s parameter_space (inline JSON or file path).')
@click.option("--search", type=click.Choice(["grid", "random", "latin"]), default="grid")
@click.option("--max-trials", type=int, default=50)
@click.option("--parallelism", type=int, default=1)
@click.option("--seed", type=int, default=0)
@click.pass_context
def cmd_sweep(ctx, session_id, manifest, base_config, parameter_space, search, max_trials, parallelism, seed):
    """Run a hyperparameter sweep under an existing session."""
    payload = {
        "manifest_path": manifest,
        "base_config": _parse_json_or_yaml_or_file(base_config),
        "search": search,
        "max_trials": max_trials,
        "parallelism": parallelism,
        "seed": seed,
    }
    if parameter_space:
        payload["parameter_space"] = _parse_json_or_yaml_or_file(parameter_space)

    async def go():
        c = _client(ctx)
        try:
            return await c.post(f"/api/research/sessions/{session_id}/sweep", json=payload)
        finally:
            await c.aclose()
    body = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        click.echo(f"Sweep done: {body['n_configs']} configs, {len(body['run_ids'])} runs.")


@research_group.command("walk-forward")
@click.option("--session-id", type=int, required=True)
@click.option("--manifest", required=True)
@click.option("--base-config", required=True)
@click.option("--parameter-space", default=None)
@click.option("--train-years", type=float, default=4.0)
@click.option("--test-years", type=float, default=1.0)
@click.option("--step-months", type=float, default=6.0)
@click.option("--objective", type=click.Choice(["sharpe", "calmar", "sortino"]), default="sharpe")
@click.option("--parallelism", type=int, default=1)
@click.pass_context
def cmd_walk_forward(ctx, session_id, manifest, base_config, parameter_space, train_years, test_years, step_months, objective, parallelism):
    """Run a walk-forward optimization under an existing session."""
    payload = {
        "manifest_path": manifest,
        "base_config": _parse_json_or_yaml_or_file(base_config),
        "train_years": train_years,
        "test_years": test_years,
        "step_months": step_months,
        "objective": objective,
        "parallelism": parallelism,
    }
    if parameter_space:
        payload["parameter_space"] = _parse_json_or_yaml_or_file(parameter_space)

    async def go():
        c = _client(ctx)
        try:
            return await c.post(f"/api/research/sessions/{session_id}/walk-forward", json=payload)
        finally:
            await c.aclose()
    body = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        click.echo(f"Walk-forward done: {body['n_folds']} folds, OOS runs: {body['oos_run_ids']}")


@research_group.command("report")
@click.option("--session-id", type=int, required=True)
@click.option("--out-dir", default="data/research_reports")
@click.pass_context
def cmd_report(ctx, session_id, out_dir):
    """Build the markdown + HTML report for a completed session."""
    async def go():
        c = _client(ctx)
        try:
            return await c.post(f"/api/research/sessions/{session_id}/report?out_dir={out_dir}", json={})
        finally:
            await c.aclose()
    body = _run(go())
    if ctx.obj.get("json_mode"):
        print_json(body)
    else:
        click.echo(f"Report written: {body['html_path']}")


def _parse_json_or_yaml_or_file(s: str):
    """Accept either inline JSON, a path to a JSON file, or a path to a YAML file.

    YAML is allowed for convenience (e.g., hyperparameters.yaml from a strategy
    package). Inline JSON is the most common form.
    """
    if not isinstance(s, str):
        return s
    p = Path(s)
    if p.exists() and p.is_file():
        text = p.read_text()
        if p.suffix in (".yaml", ".yml"):
            import yaml
            return yaml.safe_load(text)
        return json.loads(text)
    return json.loads(s)
