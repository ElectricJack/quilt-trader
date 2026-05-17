import click


@click.group(invoke_without_command=False)
@click.option("--coord", "coord_url", default=None,
              help="Coordinator URL (overrides config and env).")
@click.option("--json", "json_mode", is_flag=True, default=False,
              help="Emit JSON output where applicable.")
@click.option("-q", "--quiet", is_flag=True, default=False,
              help="Suppress non-essential output.")
@click.pass_context
def quilt(ctx: click.Context, coord_url, json_mode, quiet) -> None:
    """QuiltTrader CLI."""
    ctx.ensure_object(dict)
    ctx.obj["coord_url"] = coord_url
    ctx.obj["json_mode"] = json_mode
    ctx.obj["quiet"] = quiet


@quilt.command("version")
def version_cmd() -> None:
    """Print the CLI version."""
    try:
        from importlib.metadata import version as _v
        v = _v("quilt-trader")
    except Exception:
        v = "0.1.0"
    click.echo(f"quilt {v}")


# Legacy dev group — kept for one release for backwards compat.
@quilt.group("dev")
def dev() -> None:
    """Legacy alias group. Use top-level commands instead."""


from sdk.cli.validate import validate_cmd
dev.add_command(validate_cmd)

# Also register validate at the top level
quilt.add_command(validate_cmd)

from pathlib import Path
from sdk.cli.commands.coord import coord_group, coord_start, coord_stop
quilt.add_command(coord_group)

from sdk.cli.commands.dashboard import dashboard_group
from sdk.cli.commands.db import db_group
quilt.add_command(dashboard_group)
quilt.add_command(db_group)

from sdk.cli.commands.init import init_cmd
from sdk.cli.commands.doctor import doctor_cmd
quilt.add_command(init_cmd)
quilt.add_command(doctor_cmd)

from sdk.cli.commands.algorithm import algorithm_group
from sdk.cli.commands.worker import worker_group
from sdk.cli.commands.account import account_group
from sdk.cli.commands.settings import settings_group
quilt.add_command(algorithm_group)
quilt.add_command(algorithm_group, name="algo")
quilt.add_command(worker_group)
quilt.add_command(account_group)
quilt.add_command(settings_group)


@quilt.command("up")
@click.pass_context
def up_cmd(ctx):
    """Shortcut for `quilt coord start`."""
    dashboard_dist = Path("dashboard/dist/index.html")
    if not dashboard_dist.exists():
        from sdk.cli.output import print_status
        print_status("warning: dashboard not built; run `quilt dashboard build`")
    ctx.invoke(coord_start)


@quilt.command("down")
def down_cmd():
    """Shortcut for `quilt coord stop`."""
    coord_stop.callback()
