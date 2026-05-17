"""db commands: migrate, status, revisions."""
import subprocess
import click

from sdk.cli.output import fail


@click.group("db")
def db_group() -> None:
    """Database migrations."""


def _alembic(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["alembic", "-c", "alembic.ini"] + args,
                          capture_output=True)


@db_group.command("migrate")
def db_migrate():
    """alembic upgrade head."""
    r = _alembic(["upgrade", "head"])
    click.echo(r.stdout.decode(errors="replace"))
    if r.returncode != 0:
        click.echo(r.stderr.decode(errors="replace"), err=True)
        fail(4, "alembic upgrade failed")


@db_group.command("status")
def db_status():
    """alembic current."""
    r = _alembic(["current"])
    click.echo(r.stdout.decode(errors="replace"))
    if r.returncode != 0:
        fail(4, "alembic current failed")


@db_group.command("revisions")
def db_revisions():
    """alembic history."""
    r = _alembic(["history"])
    click.echo(r.stdout.decode(errors="replace"))
    if r.returncode != 0:
        fail(4, "alembic history failed")
