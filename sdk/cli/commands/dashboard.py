"""dashboard commands: build, dev."""
import os
import subprocess
import click

from sdk.cli.output import fail


@click.group("dashboard")
def dashboard_group() -> None:
    """Dashboard build / dev server."""


@dashboard_group.command("build")
def dashboard_build():
    """Run `npm run build` in the dashboard directory."""
    result = subprocess.run(["npm", "run", "build"], cwd="dashboard")
    if result.returncode != 0:
        fail(4, "dashboard build failed")
    click.echo("dashboard built to dashboard/dist/")


@dashboard_group.command("dev")
def dashboard_dev():
    """Run `npm run dev` in foreground (HMR mode)."""
    os.execvp("npm", ["npm", "run", "dev", "--prefix", "dashboard"])
