import os
import pytest
from unittest.mock import MagicMock, patch
import pandas as pd
from coordinator.services.scraper_engine import ScraperEngine, ScraperResult

@pytest.fixture
def packages_dir(tmp_path):
    pkg = tmp_path / "packages" / "alpha-picks-scraper"
    pkg.mkdir(parents=True)
    (pkg / "quilt.yaml").write_text(
        "name: alpha-picks-scraper\ntype: scraper\nschedule: '*/30 * * * *'\n"
    )
    (pkg / "scraper.py").write_text(
        "from sdk.scraper import QuiltScraper\nimport pandas as pd\n"
        "class AlphaPicksScraper(QuiltScraper):\n"
        "    def on_run(self):\n"
        "        return pd.DataFrame({'symbol': ['TSLA'], 'score': [0.9]})\n"
    )
    return str(tmp_path / "packages")

@pytest.fixture
def output_dir(tmp_path):
    d = tmp_path / "custom"
    d.mkdir()
    return str(d)

def test_scraper_engine_init(packages_dir, output_dir):
    engine = ScraperEngine(packages_dir=packages_dir, output_dir=output_dir)
    assert engine is not None

def test_parse_scraper_manifest(packages_dir, output_dir):
    engine = ScraperEngine(packages_dir=packages_dir, output_dir=output_dir)
    manifest = engine.parse_manifest("alpha-picks-scraper")
    assert manifest["name"] == "alpha-picks-scraper"
    assert manifest["type"] == "scraper"

def test_output_path(packages_dir, output_dir):
    engine = ScraperEngine(packages_dir=packages_dir, output_dir=output_dir)
    path = engine.output_path("alpha-picks-scraper", "csv")
    assert path.endswith("alpha-picks-scraper.csv")

@patch("coordinator.services.scraper_engine.subprocess")
def test_run_scraper(mock_subprocess, packages_dir, output_dir):
    mock_subprocess.run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    engine = ScraperEngine(packages_dir=packages_dir, output_dir=output_dir)
    result = engine.run_scraper("alpha-picks-scraper", "csv")
    assert isinstance(result, ScraperResult)
    assert result.success is True

@patch("coordinator.services.scraper_engine.subprocess")
def test_run_scraper_failure(mock_subprocess, packages_dir, output_dir):
    mock_subprocess.run.return_value = MagicMock(
        returncode=1, stdout="", stderr="ImportError: No module named 'selenium'",
    )
    engine = ScraperEngine(packages_dir=packages_dir, output_dir=output_dir)
    result = engine.run_scraper("alpha-picks-scraper", "csv")
    assert result.success is False
    assert "selenium" in result.error

@patch("coordinator.services.scraper_engine.subprocess")
def test_run_scraper_passes_config_to_subprocess(mock_subprocess, packages_dir, output_dir):
    mock_subprocess.run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    engine = ScraperEngine(packages_dir=packages_dir, output_dir=output_dir)
    engine.run_scraper("alpha-picks-scraper", "csv", config={"cookies_file": "/tmp/c.json", "headless": True})
    args, kwargs = mock_subprocess.run.call_args
    runner_script = args[0][2]
    assert '"cookies_file": "/tmp/c.json"' in runner_script
    assert '"headless": true' in runner_script
    assert "json.loads(" in runner_script

import sys as _sys
import venv as _venv

@pytest.mark.slow
def test_run_scraper_real_subprocess_can_import_sdk(tmp_path):
    pkg = tmp_path / "packages" / "stub-scraper"
    pkg.mkdir(parents=True)
    (pkg / "quilt.yaml").write_text(
        "name: stub-scraper\ntype: scraper\nschedule: '*/30 * * * *'\n"
    )
    (pkg / "scraper.py").write_text(
        "from sdk.scraper import QuiltScraper\n"
        "import pandas as pd\n"
        "class Scraper(QuiltScraper):\n"
        "    def on_run(self):\n"
        "        return pd.DataFrame({'a': [1, 2]})\n"
    )
    venv_dir = pkg / ".venv"
    _venv.create(str(venv_dir), with_pip=True)
    pip = venv_dir / "bin" / "pip"
    import subprocess as _sub
    r = _sub.run([str(pip), "install", "--quiet", "pandas", "pyyaml"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    out_dir = tmp_path / "custom"
    out_dir.mkdir()
    engine = ScraperEngine(packages_dir=str(tmp_path / "packages"), output_dir=str(out_dir))
    result = engine.run_scraper("stub-scraper", "csv")
    assert result.success is True, f"stderr was: {result.error}"
    assert (out_dir / "stub-scraper.csv").exists()
    csv = (out_dir / "stub-scraper.csv").read_text()
    assert "1\n2" in csv
