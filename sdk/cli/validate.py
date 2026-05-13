from __future__ import annotations
import importlib.util
import sys
from pathlib import Path
import click
from sdk.manifest import QuiltManifest, ManifestError

def validate_package(package_path: Path) -> list[str]:
    errors: list[str] = []
    manifest_path = package_path / "quilt.yaml"
    if not manifest_path.exists():
        return [f"No quilt.yaml found in {package_path}"]
    try:
        manifest = QuiltManifest.from_file(manifest_path)
    except ManifestError as e:
        return [f"Invalid quilt.yaml: {e}"]
    if manifest.type == "algorithm":
        errors.extend(_validate_algorithm_entry(package_path, manifest))
    return errors

def _validate_algorithm_entry(package_path: Path, manifest: QuiltManifest) -> list[str]:
    errors: list[str] = []
    entry_file = package_path / manifest.entry_point
    if not entry_file.exists():
        return [f"Entry point file not found: {manifest.entry_point}"]
    module_name = f"_quilt_validate_{manifest.name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, entry_file)
    if spec is None or spec.loader is None:
        return [f"Cannot load module from {manifest.entry_point}"]
    module = importlib.util.module_from_spec(spec)
    old_path = sys.path.copy()
    sys.path.insert(0, str(package_path))
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        return [f"Error importing {manifest.entry_point}: {e}"]
    finally:
        sys.path = old_path
    cls = getattr(module, manifest.class_name, None)
    if cls is None:
        errors.append(f"Class '{manifest.class_name}' not found in {manifest.entry_point}")
        return errors
    from sdk.algorithm import QuiltAlgorithm
    if not issubclass(cls, QuiltAlgorithm):
        errors.append(f"Class '{manifest.class_name}' does not extend QuiltAlgorithm")
    return errors

@click.command("validate")
@click.option("--path", type=click.Path(exists=True, file_okay=False, path_type=Path), default=".", help="Path to the algorithm/scraper package directory.")
def validate_cmd(path: Path):
    """Validate a quilt.yaml manifest and entry point."""
    errors = validate_package(path)
    if errors:
        for error in errors:
            click.echo(f"FAIL: {error}", err=True)
        raise SystemExit(1)
    manifest = QuiltManifest.from_file(path / "quilt.yaml")
    click.echo(f"PASS: {manifest.name} ({manifest.type}) v{manifest.version} is valid")
