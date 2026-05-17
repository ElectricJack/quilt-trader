"""quilt init — first-time setup."""
from __future__ import annotations

import subprocess
from pathlib import Path

import click
import yaml

from sdk.cli.config import quilt_home
from sdk.cli.output import fail


@click.command("init")
@click.option("--coord-url", default="http://localhost:8000")
@click.option("--data-dir", default="./data")
@click.option("--db-url", default="sqlite+aiosqlite:///data/quilt_trader.db")
@click.option("--force", is_flag=True, default=False)
@click.option("--skip-migrate", is_flag=True, default=False)
def init_cmd(coord_url, data_dir, db_url, force, skip_migrate):
    """First-time Quilt setup."""
    home = quilt_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "run").mkdir(exist_ok=True)
    (home / "log").mkdir(exist_ok=True)
    config_path = home / "config.yaml"
    if config_path.exists() and not force:
        fail(2, f"config already exists at {config_path}; use --force to overwrite")
    config_path.write_text(yaml.safe_dump({
        "coordinator_url": coord_url,
        "data_dir": data_dir,
        "db_url": db_url,
    }))
    click.echo(f"wrote {config_path}")

    Path(data_dir).mkdir(parents=True, exist_ok=True)
    Path(data_dir, "packages").mkdir(exist_ok=True)

    if not skip_migrate:
        import sys as _sys
        r = subprocess.run(
            [_sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
            capture_output=True,
        )
        if r.returncode != 0:
            click.echo(r.stderr.decode(errors="replace"), err=True)
            fail(4, "alembic upgrade failed during init")
        click.echo("database migrations applied")

    click.echo(
        "\nQuilt initialized. Next steps:\n"
        "  quilt worker add --name pi-1\n"
        "  quilt algorithm install <repo-or-path>\n"
        "  quilt up"
    )
