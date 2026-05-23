# tests/coordinator/services/test_data_validation.py
import pytest
from pathlib import Path
from coordinator.services.backtest_runner import _validate_custom_data_deps

def test_validate_passes_when_data_exists(tmp_path):
    custom_dir = tmp_path / "data" / "custom"
    custom_dir.mkdir(parents=True)
    (custom_dir / "alpha-picks-scraper.csv").write_text("col1,col2\n1,2\n")
    data_deps = [{"source": "alpha-picks-scraper", "type": "csv"}]
    _validate_custom_data_deps(data_deps, custom_dir)

def test_validate_raises_when_data_missing(tmp_path):
    custom_dir = tmp_path / "data" / "custom"
    custom_dir.mkdir(parents=True)
    data_deps = [{"source": "nonexistent-scraper", "type": "scraper"}]
    with pytest.raises(FileNotFoundError, match="nonexistent-scraper"):
        _validate_custom_data_deps(data_deps, custom_dir)

def test_validate_finds_subdirectory_match(tmp_path):
    custom_dir = tmp_path / "data" / "custom"
    subdir = custom_dir / "alpha-picks-scraper"
    subdir.mkdir(parents=True)
    (subdir / "data.csv").write_text("col1\n1\n")
    data_deps = [{"source": "alpha-picks-scraper", "type": "scraper"}]
    _validate_custom_data_deps(data_deps, custom_dir)

def test_validate_empty_deps_is_noop(tmp_path):
    custom_dir = tmp_path / "data" / "custom"
    _validate_custom_data_deps([], custom_dir)
