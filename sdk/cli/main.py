import click

@click.group()
def quilt():
    """QuiltTrader SDK — algorithm development tools."""
    pass

@quilt.group()
def dev():
    """Local development commands for algorithm authors."""
    pass

from sdk.cli.validate import validate_cmd
dev.add_command(validate_cmd)
