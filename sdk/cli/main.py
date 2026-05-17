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
