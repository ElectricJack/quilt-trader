import pytest
from pathlib import Path
from sdk.validation import validate_algorithm_package, ValidationError


def _write_pkg(tmp_path, manifest_yaml: str, files: dict[str, str]) -> None:
    (tmp_path / "quilt.yaml").write_text(manifest_yaml)
    for relpath, content in files.items():
        full = tmp_path / relpath
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)


def test_valid_package_returns_empty_errors(tmp_path):
    _write_pkg(tmp_path,
        """name: test
type: algorithm
version: 1.0.0
entry_point: my_algo
class_name: MyAlgo
requirements:
  asset_types: [equities]
""",
        {"my_algo.py":
            "from sdk.algorithm import QuiltAlgorithm\n"
            "class MyAlgo(QuiltAlgorithm):\n"
            "    def on_start(self, c, s): pass\n"
            "    def on_tick(self, ctx): return []\n"
            "    def on_stop(self): return {}\n"
            "    def save_state(self): return {}\n"
        },
    )
    errors = validate_algorithm_package(tmp_path)
    assert errors == []


def test_missing_manifest_errors(tmp_path):
    errors = validate_algorithm_package(tmp_path)
    assert errors
    assert any("quilt.yaml" in str(e) for e in errors)


def test_class_does_not_extend_base_errors(tmp_path):
    _write_pkg(tmp_path,
        """name: test
type: algorithm
version: 1.0.0
entry_point: my_algo
class_name: NotAnAlgo
requirements:
  asset_types: [equities]
""",
        {"my_algo.py": "class NotAnAlgo:\n    pass\n"},
    )
    errors = validate_algorithm_package(tmp_path)
    assert any("QuiltAlgorithm" in str(e) for e in errors)


def test_class_name_missing_in_module(tmp_path):
    _write_pkg(tmp_path,
        """name: test
type: algorithm
version: 1.0.0
entry_point: my_algo
class_name: NotPresent
requirements:
  asset_types: [equities]
""",
        {"my_algo.py": "# nothing here\n"},
    )
    errors = validate_algorithm_package(tmp_path)
    assert any("not found" in str(e).lower() for e in errors)


def test_invalid_manifest_yaml_returns_error(tmp_path):
    (tmp_path / "quilt.yaml").write_text("name: test\ntype: invalid_type\n")
    errors = validate_algorithm_package(tmp_path)
    assert errors
