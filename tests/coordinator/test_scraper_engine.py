import json
import os
import pytest
from unittest.mock import MagicMock, patch
import pandas as pd
from coordinator.services.scraper_engine import ScraperEngine, ScraperResult


def _runner(result=None, returncode=0, stderr=""):
    """A subprocess.run stand-in that behaves like sdk.scraper_runner.

    Writes `result` to the --result path it was given (None: writes nothing,
    like a runner killed before it could) and exits with `returncode`.
    """
    def fake_run(cmd, **kwargs):
        if result is not None:
            with open(cmd[cmd.index("--result") + 1], "w") as f:
                json.dump(result, f)
        return MagicMock(returncode=returncode, stdout="", stderr=stderr)
    return fake_run


RUNNER_OK = _runner({"status": "ok", "rows": 1})

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
    mock_subprocess.run.side_effect = RUNNER_OK
    engine = ScraperEngine(packages_dir=packages_dir, output_dir=output_dir)
    result = engine.run_scraper("alpha-picks-scraper", "csv")
    assert isinstance(result, ScraperResult)
    assert result.success is True
    assert result.error_kind is None
    assert result.output_path == engine.output_path("alpha-picks-scraper", "csv")

@patch("coordinator.services.scraper_engine.subprocess")
def test_run_scraper_failure(mock_subprocess, packages_dir, output_dir):
    mock_subprocess.run.return_value = MagicMock(
        returncode=1, stdout="", stderr="ImportError: No module named 'selenium'",
    )
    engine = ScraperEngine(packages_dir=packages_dir, output_dir=output_dir)
    result = engine.run_scraper("alpha-picks-scraper", "csv")
    assert result.success is False
    assert "selenium" in result.error
    assert result.error_kind == "error"

@patch("coordinator.services.scraper_engine.subprocess")
def test_run_scraper_passes_config_to_subprocess(mock_subprocess, packages_dir, output_dir):
    mock_subprocess.run.side_effect = RUNNER_OK
    engine = ScraperEngine(packages_dir=packages_dir, output_dir=output_dir)
    engine.run_scraper("alpha-picks-scraper", "csv", config={"cookies_file": "/tmp/c.json", "headless": True})
    args, kwargs = mock_subprocess.run.call_args
    cmd = args[0]
    config = json.loads(cmd[cmd.index("--config-json") + 1])
    assert config == {"cookies_file": "/tmp/c.json", "headless": True}


@patch("coordinator.services.scraper_engine.subprocess")
def test_run_scraper_launches_the_runner_module(mock_subprocess, packages_dir, output_dir):
    mock_subprocess.run.side_effect = RUNNER_OK
    engine = ScraperEngine(packages_dir=packages_dir, output_dir=output_dir, quilt_root="/q/root")
    engine.run_scraper("alpha-picks-scraper", "csv")
    cmd = mock_subprocess.run.call_args.args[0]
    kwargs = mock_subprocess.run.call_args.kwargs
    pkg_dir = os.path.join(packages_dir, "alpha-picks-scraper")
    assert cmd[1] == "-c"
    assert cmd[2] == (
        f"import sys; sys.path.insert(0, {pkg_dir!r}); sys.path.append('/q/root'); "
        f"from sdk.scraper_runner import main; sys.exit(main())"
    )
    assert cmd[cmd.index("--pkg-dir") + 1] == pkg_dir
    assert cmd[cmd.index("--out") + 1] == engine.output_path("alpha-picks-scraper", "csv")
    result_path = cmd[cmd.index("--result") + 1]
    assert not os.path.exists(result_path)  # the engine cleans up its result file
    assert kwargs["cwd"] == pkg_dir


# --- error_kind mapping from the runner's result file --------------------------

@pytest.mark.parametrize("runner, success, kind, error", [
    (_runner({"status": "ok", "rows": 3}), True, None, None),
    (_runner({"status": "auth_required", "message": "session expired"}, 2, "AuthExpiredError: x"),
     False, "auth_required", "session expired"),
    (_runner({"status": "bot_blocked", "message": "PerimeterX 403"}, 2),
     False, "bot_blocked", "PerimeterX 403"),
    (_runner({"status": "profile_busy",
              "holder": {"pid": 1234, "role": "login", "started_at": "2026-09-28T14:02:00+00:00"},
              "message": "profile /p is in use by login session, pid 1234, since 14:02 UTC"}, 3),
     False, "profile_busy", "profile /p is in use by login session, pid 1234, since 14:02 UTC"),
    (_runner({"status": "error", "error_type": "ValueError", "message": "bad table"}, 1,
             "Traceback (most recent call last):\nValueError: bad table\n"),
     False, "error", "Traceback (most recent call last):\nValueError: bad table\n"),
    (_runner({"status": "error", "error_type": "ValueError", "message": "bad table"}, 1, ""),
     False, "error", "ValueError: bad table"),
    # No result file (segfault, OOM kill): the stderr tail, as before.
    (_runner(None, -9, "Fatal Python error: Segmentation fault"),
     False, "error", "Fatal Python error: Segmentation fault"),
    (_runner(None, -9, ""), False, "error", "scraper runner exited with code -9 and no result"),
    # Exit 0 without a result file is not a success (e.g. the scraper called sys.exit).
    (_runner(None, 0, ""), False, "error", "scraper runner exited with code 0 and no result"),
])
@patch("coordinator.services.scraper_engine.subprocess")
def test_run_scraper_error_kind_mapping(
    mock_subprocess, runner, success, kind, error, packages_dir, output_dir,
):
    mock_subprocess.run.side_effect = runner
    engine = ScraperEngine(packages_dir=packages_dir, output_dir=output_dir)
    result = engine.run_scraper("alpha-picks-scraper", "csv")
    assert result.success is success
    assert result.error_kind == kind
    assert result.error == error
    if success:
        assert result.output_path.endswith("alpha-picks-scraper.csv")


@patch("coordinator.services.scraper_engine.subprocess")
def test_run_scraper_garbled_result_file_is_an_error(mock_subprocess, packages_dir, output_dir):
    def fake_run(cmd, **kwargs):
        with open(cmd[cmd.index("--result") + 1], "w") as f:
            f.write("{not json")
        return MagicMock(returncode=0, stdout="", stderr="")
    mock_subprocess.run.side_effect = fake_run
    engine = ScraperEngine(packages_dir=packages_dir, output_dir=output_dir)
    result = engine.run_scraper("alpha-picks-scraper", "csv")
    assert result.success is False
    assert result.error_kind == "error"

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


# --- Real runner subprocess (the coordinator's own interpreter) ---------------
# No package venv, so run_scraper falls back to sys.executable. PYTHONPATH pins
# this checkout's sdk ahead of any installed copy of quilt-trader.

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def real_runner_pkgs(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", _REPO_ROOT)
    root = tmp_path / "real-packages"
    pkg = root / "typed-scraper"
    pkg.mkdir(parents=True)
    (pkg / "quilt.yaml").write_text(
        "name: typed-scraper\ntype: scraper\nschedule: '0 14 * * 1-5'\n"
        "config:\n  parameters:\n"
        "    - name: profile_dir\n      type: string\n      default: profile\n"
        "auth:\n  kind: browser_profile\n  profile_dir_param: profile_dir\n"
        "  login_url: https://example.com/login\n  verify:\n    selector: table\n"
    )
    (pkg / "scraper.py").write_text(
        "import pandas as pd\n"
        "from sdk.scraper import AuthRequired, QuiltScraper\n"
        "class AuthExpiredError(AuthRequired):\n    pass\n"
        "class Scraper(QuiltScraper):\n"
        "    def on_start(self, config):\n        self.mode = config.get('mode')\n"
        "    def on_run(self):\n"
        "        if self.mode == 'auth':\n"
        "            raise AuthExpiredError('login wall at /alpha-picks')\n"
        "        return pd.DataFrame({'a': [1, 2]})\n"
    )
    return root


def test_real_runner_ok(real_runner_pkgs, output_dir):
    engine = ScraperEngine(packages_dir=str(real_runner_pkgs), output_dir=output_dir)
    result = engine.run_scraper("typed-scraper", "csv", config={})
    assert result.success is True, result.error
    assert result.error_kind is None
    assert open(result.output_path).read().splitlines() == ["a", "1", "2"]


def test_real_runner_auth_required(real_runner_pkgs, output_dir):
    engine = ScraperEngine(packages_dir=str(real_runner_pkgs), output_dir=output_dir)
    result = engine.run_scraper("typed-scraper", "csv", config={"mode": "auth"})
    assert result.success is False
    assert result.error_kind == "auth_required"
    assert result.error == "login wall at /alpha-picks"


def test_real_runner_profile_busy(real_runner_pkgs, output_dir):
    from sdk.scraper_auth import profile_lock

    engine = ScraperEngine(packages_dir=str(real_runner_pkgs), output_dir=output_dir)
    profile = str(real_runner_pkgs / "typed-scraper" / "profile")
    with profile_lock(profile, "login"):
        result = engine.run_scraper("typed-scraper", "csv", config={})
    assert result.success is False
    assert result.error_kind == "profile_busy"
    assert f"in use by login session, pid {os.getpid()}" in result.error


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
    mock_subprocess.run.side_effect = RUNNER_OK
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
    assert result.error_kind == "error"
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
    mock_subprocess.run.side_effect = RUNNER_OK
    engine = _engine(packages_dir, output_dir, live_x_server)
    result = engine.run_scraper("alpha-picks-scraper", "csv", config={"headless": False})
    assert result.success is True
    env = mock_subprocess.run.call_args.kwargs.get("env")
    assert env is None or env["DISPLAY"] == ":7"


@patch("coordinator.services.scraper_engine.subprocess")
def test_headless_run_does_not_touch_display(
    mock_subprocess, packages_dir, output_dir, live_x_server, no_display,
):
    mock_subprocess.run.side_effect = RUNNER_OK
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
    mock_subprocess.run.side_effect = RUNNER_OK
    result = engine.run_scraper("headed-scraper", "csv", config={"headless": True})
    assert result.success is True


# --- Shared headed/display resolution (reused by login sessions) -------------

from coordinator.services.scraper_engine import child_env, runs_headed


def test_runs_headed_module_function():
    manifest = {"config": {"parameters": [{"name": "headless", "type": "bool", "default": False}]}}
    assert runs_headed(manifest, {}) is True
    assert runs_headed(manifest, {"headless": True}) is False
    assert runs_headed({}, {"headless": False}) is True
    assert runs_headed(None, {}) is False
    assert runs_headed({"config": "garbage"}, {}) is False


def test_child_env_module_function(live_x_server, x11_dir, no_display):
    headed = {"config": {"parameters": [{"name": "headless", "default": False}]}}
    env, err = child_env("demo", headed, {}, x11_socket_dir=live_x_server)
    assert err is None and env["DISPLAY"] == ":3"
    assert child_env("demo", {}, {}, x11_socket_dir=live_x_server) == (None, None)


def test_child_env_module_function_without_display(x11_dir, no_display):
    env, err = child_env("demo", None, {"headless": False}, x11_socket_dir=x11_dir)
    assert env is None
    assert "scraper demo runs a headed browser" in err
