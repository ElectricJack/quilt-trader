from __future__ import annotations
from pathlib import Path
import click
from sdk.manifest import QuiltManifest
from sdk.validation import validate_algorithm_package


@click.command("validate")
@click.option("--path", type=click.Path(exists=True, file_okay=False, path_type=Path),
              default=".", help="Path to the algorithm/scraper package directory.")
def validate_cmd(path: Path) -> None:
    """Validate a quilt.yaml manifest and entry point."""
    errors = validate_algorithm_package(path)
    if errors:
        for error in errors:
            click.echo(f"FAIL: {error}", err=True)
        raise SystemExit(2)
    manifest = QuiltManifest.from_file(path / "quilt.yaml")
    click.echo(f"PASS: {manifest.name} ({manifest.type}) v{manifest.version} is valid")
