"""Shared algorithm package validation.

Used by:
- `quilt validate <path>` (CLI)
- POST /api/algorithms/install-from-url (coordinator route)

Performs 4 cheap checks: manifest exists & parses, entry-point module imports,
class exists, class extends QuiltAlgorithm.
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

from sdk.manifest import QuiltManifest, ManifestError


@dataclass
class ValidationError:
    message: str

    def __str__(self) -> str:
        return self.message


def validate_algorithm_package(package_path: Path) -> list[ValidationError]:
    package_path = Path(package_path)
    manifest_path = package_path / "quilt.yaml"
    if not manifest_path.exists():
        return [ValidationError(f"No quilt.yaml found in {package_path}")]
    try:
        manifest = QuiltManifest.from_file(manifest_path)
    except ManifestError as e:
        return [ValidationError(f"Invalid quilt.yaml: {e}")]
    if manifest.type != "algorithm":
        return []
    return _validate_algorithm_entry(package_path, manifest)


def _entry_point_to_relpath(entry_point: str) -> str:
    """Convert an entry_point value to a relative .py file path.

    Accepts two formats:
    - ``"my_algo"``        → ``"my_algo.py"``         (dotted module name)
    - ``"my_algo.module"`` → ``"my_algo/module.py"``   (nested dotted module name)
    - ``"algo.py"``        → ``"algo.py"``              (already a filename, pass through)
    """
    if entry_point.endswith(".py"):
        return entry_point
    return entry_point.replace(".", "/") + ".py"


def _validate_algorithm_entry(package_path: Path, manifest: QuiltManifest) -> list[ValidationError]:
    entry_relpath = _entry_point_to_relpath(manifest.entry_point)
    entry_file = package_path / entry_relpath
    if not entry_file.exists():
        return [ValidationError(f"Entry point file not found: {entry_relpath}")]
    module_name = f"_quilt_validate_{manifest.name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, entry_file)
    if spec is None or spec.loader is None:
        return [ValidationError(f"Cannot load module from {entry_relpath}")]
    module = importlib.util.module_from_spec(spec)
    old_path = sys.path.copy()
    sys.path.insert(0, str(package_path))
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        return [ValidationError(f"Error importing {entry_relpath}: {e}")]
    finally:
        sys.path = old_path
    cls = getattr(module, manifest.class_name, None)
    if cls is None:
        return [ValidationError(f"Class '{manifest.class_name}' not found in {entry_relpath}")]
    from sdk.algorithm import QuiltAlgorithm
    if not issubclass(cls, QuiltAlgorithm):
        return [ValidationError(f"Class '{manifest.class_name}' does not extend QuiltAlgorithm")]
    return []
