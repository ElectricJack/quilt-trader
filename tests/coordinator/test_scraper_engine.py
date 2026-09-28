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


def test_scraper_engine_quilt_root_explicit_override(packages_dir, output_dir):
    engine = ScraperEngine(packages_dir=packages_dir, output_dir=output_dir, quilt_root="/custom/root")
    assert engine._quilt_root == "/custom/root"


def test_scraper_engine_quilt_root_autodetect(packages_dir, output_dir):
    import coordinator.services.scraper_engine as eng_module
    engine = ScraperEngine(packages_dir=packages_dir, output_dir=output_dir)
    expected = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(eng_module.__file__))))
    assert engine._quilt_root == expected


# --- Headed runs need an X display -------------------------------------------
# A scraper configured headless=false launches a visible Chromium. When the
# coordinator is started by cron (the watchdog), its environment has no
# DISPLAY, so Chromium exits at launch ("Missing X server or $DISPLAY") and
# patchright surfaces an opaque TargetClosedError. The engine now hands the
# child the host's X server when one is listening, and otherwise fails the run
# with a message that names the problem instead of launching a doomed browser.

import socket as _socket
import tempfile as _tempfile


@pytest.fixture
def x11_dir():
    # AF_UNIX paths are capped near 108 bytes, so keep this short rather than
    # nesting it under pytest's tmp_path.
    with _tempfile.TemporaryDirectory(prefix="x11-") as d:
        yield d


@pytest.fixture
def live_x_server(x11_dir):
    sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    sock.bind(os.path.join(x11_dir, "X3"))
    sock.listen(1)
    yield x11_dir
    sock.close()


@pytest.fixture
def no_display(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)


def _engine(packages_dir, output_dir, x11_dir):
    return ScraperEngine(
        packages_dir=packages_dir, output_dir=output_dir, x11_socket_dir=x11_dir,
    )


@patch("coordinator.services.scraper_engine.subprocess")
def test_headed_run_without_display_adopts_live_x_server(
    mock_subprocess, packages_dir, output_dir, live_x_server, no_display,
):
    mock_subprocess.run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    engine = _engine(packages_dir, output_dir, live_x_server)
    result = engine.run_scraper("alpha-picks-scraper", "csv", config={"headless": False})
    assert result.success is True
    env = mock_subprocess.run.call_args.kwargs["env"]
    assert env["DISPLAY"] == ":3"
    assert env["HOME"] == os.environ["HOME"]  # the rest of the environment is kept


@patch("coordinator.services.scraper_engine.subprocess")
def test_headed_run_without_any_display_fails_fast_with_clear_error(
    mock_subprocess, packages_dir, output_dir, x11_dir, no_display,
):
    engine = _engine(packages_dir, output_dir, x11_dir)
    result = engine.run_scraper("alpha-picks-scraper", "csv", config={"headless": False})
    assert result.success is False
    assert "no X display" in result.error
    assert "headless" in result.error
    mock_subprocess.run.assert_not_called()


@patch("coordinator.services.scraper_engine.subprocess")
def test_headed_run_ignores_stale_x_socket(
    mock_subprocess, packages_dir, output_dir, x11_dir, no_display,
):
    # A socket file left behind by a dead X server refuses connections.
    stale = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    stale.bind(os.path.join(x11_dir, "X0"))
    stale.close()
    engine = _engine(packages_dir, output_dir, x11_dir)
    result = engine.run_scraper("alpha-picks-scraper", "csv", config={"headless": False})
    assert result.success is False
    assert "no X display" in result.error
    mock_subprocess.run.assert_not_called()


@patch("coordinator.services.scraper_engine.subprocess")
def test_headed_run_keeps_existing_display(
    mock_subprocess, packages_dir, output_dir, live_x_server, monkeypatch,
):
    monkeypatch.setenv("DISPLAY", ":7")
    mock_subprocess.run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    engine = _engine(packages_dir, output_dir, live_x_server)
    result = engine.run_scraper("alpha-picks-scraper", "csv", config={"headless": False})
    assert result.success is True
    env = mock_subprocess.run.call_args.kwargs.get("env")
    assert env is None or env["DISPLAY"] == ":7"


@patch("coordinator.services.scraper_engine.subprocess")
def test_headless_run_does_not_touch_display(
    mock_subprocess, packages_dir, output_dir, live_x_server, no_display,
):
    mock_subprocess.run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    engine = _engine(packages_dir, output_dir, live_x_server)
    for config in (None, {"headless": True}):
        result = engine.run_scraper("alpha-picks-scraper", "csv", config=config)
        assert result.success is True
        env = mock_subprocess.run.call_args.kwargs.get("env")
        assert env is None or "DISPLAY" not in env


@patch("coordinator.services.scraper_engine.subprocess")
def test_manifest_default_headless_false_counts_as_headed(
    mock_subprocess, tmp_path, output_dir, x11_dir, no_display,
):
    pkg = tmp_path / "pkgs" / "headed-scraper"
    pkg.mkdir(parents=True)
    (pkg / "quilt.yaml").write_text(
        "name: headed-scraper\ntype: scraper\nschedule: '0 14 * * *'\n"
        "config:\n  parameters:\n"
        "    - name: headless\n      type: bool\n      default: false\n"
    )
    engine = _engine(str(tmp_path / "pkgs"), output_dir, x11_dir)
    result = engine.run_scraper("headed-scraper", "csv", config={})
    assert result.success is False
    assert "no X display" in result.error
    mock_subprocess.run.assert_not_called()

    # ...and an explicit override still wins over the manifest default.
    mock_subprocess.run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    result = engine.run_scraper("headed-scraper", "csv", config={"headless": True})
    assert result.success is True
