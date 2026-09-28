"""sdk/scraper_runner.py: result file and exit code for every outcome.

Each test runs the runner in a child process exactly as ScraperEngine does
(review rev-nimble-bridge, section 6.2).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from sdk.scraper_auth import profile_lock, read_profile_holder
from sdk.scraper_runner import EXIT_AUTH, EXIT_ERROR, EXIT_OK, EXIT_PROFILE_BUSY

REPO_ROOT = Path(__file__).resolve().parents[2]

SCRAPER_SOURCE = textwrap.dedent("""
    import os
    import pandas as pd
    from sdk.scraper import AuthRequired, BotBlocked, QuiltScraper, ScraperAuthError
    from sdk.scraper_auth import read_profile_holder


    class AuthExpiredError(AuthRequired):
        pass


    class BotBlockedError(BotBlocked):
        pass


    class Scraper(QuiltScraper):
        def _mark(self, what):
            marker_dir = self.config.get("marker_dir")
            if marker_dir:
                open(os.path.join(marker_dir, what), "w").close()

        def on_start(self, config):
            self.config = config
            self._mark("started")

        def on_run(self):
            mode = self.config.get("mode", "ok")
            if mode == "auth":
                raise AuthExpiredError("session expired; sign in again")
            if mode == "bot":
                raise BotBlockedError("PerimeterX 403 on the landing page")
            if mode == "auth_base":
                raise ScraperAuthError("some auth problem")
            if mode == "boom":
                raise ValueError("could not parse the picks table")
            if mode == "holder":
                holder = read_profile_holder(self.config.get("profile_dir", "profile")) or {}
                return pd.DataFrame([{"pid": holder.get("pid"), "role": holder.get("role")}])
            return pd.DataFrame({"symbol": ["AAPL", "MSFT", "NVDA"], "score": [0.8, 0.6, 0.5]})

        def on_stop(self):
            self._mark("stopped")
""")


def _make_package(root: Path, *, auth: dict | None = None, source: str = SCRAPER_SOURCE) -> Path:
    pkg = root / "pkg"
    pkg.mkdir()
    manifest = {
        "name": "demo-scraper",
        "type": "scraper",
        "schedule": "0 14 * * 1-5",
        "entry_point": "scraper.py",
        "class_name": "Scraper",
        "config": {"parameters": [
            {"name": "profile_dir", "type": "string", "default": "profile"},
        ]},
    }
    if auth is not None:
        manifest["auth"] = auth
    (pkg / "quilt.yaml").write_text(yaml.safe_dump(manifest))
    (pkg / "scraper.py").write_text(source)
    return pkg


AUTH_BLOCK = {
    "kind": "browser_profile",
    "profile_dir_param": "profile_dir",
    "login_url": "https://example.com/login",
    "verify": {"selector": "table.picks"},
}


def _run(pkg: Path, tmp_path: Path, config: dict | str):
    out = tmp_path / "out.csv"
    result = tmp_path / "result.json"
    bootstrap = (
        f"import sys; sys.path.insert(0, {str(pkg)!r}); sys.path.append({str(REPO_ROOT)!r}); "
        f"from sdk.scraper_runner import main; sys.exit(main())"
    )
    config_json = config if isinstance(config, str) else json.dumps(config)
    # PYTHONPATH pins this checkout's sdk ahead of any installed copy.
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    proc = subprocess.run(
        [sys.executable, "-c", bootstrap, "--pkg-dir", str(pkg), "--config-json", config_json,
         "--out", str(out), "--result", str(result)],
        capture_output=True, text=True, cwd=str(pkg), env=env, timeout=120,
    )
    data = json.loads(result.read_text()) if result.exists() else None
    return proc, data, out


@pytest.fixture
def markers(tmp_path):
    d = tmp_path / "markers"
    d.mkdir()
    return d


def test_ok_writes_csv_and_row_count(tmp_path, markers):
    pkg = _make_package(tmp_path)
    proc, result, out = _run(pkg, tmp_path, {"marker_dir": str(markers)})
    assert proc.returncode == EXIT_OK == 0, proc.stderr
    assert result == {"status": "ok", "rows": 3}
    assert out.read_text().splitlines()[0] == "symbol,score"
    assert (markers / "started").exists() and (markers / "stopped").exists()
    assert not list(tmp_path.glob(".scraper-result-*"))  # no temp file left behind


def test_auth_required(tmp_path, markers):
    pkg = _make_package(tmp_path)
    proc, result, out = _run(pkg, tmp_path, {"mode": "auth", "marker_dir": str(markers)})
    assert proc.returncode == EXIT_AUTH == 2
    assert result == {"status": "auth_required", "message": "session expired; sign in again"}
    assert "AuthExpiredError: session expired" in proc.stderr
    assert not out.exists()
    assert not (markers / "stopped").exists()  # on_stop runs only after success, as before


def test_bot_blocked(tmp_path):
    pkg = _make_package(tmp_path)
    proc, result, _ = _run(pkg, tmp_path, {"mode": "bot"})
    assert proc.returncode == EXIT_AUTH == 2
    assert result == {"status": "bot_blocked", "message": "PerimeterX 403 on the landing page"}


def test_base_scraper_auth_error_counts_as_auth_required(tmp_path):
    pkg = _make_package(tmp_path)
    proc, result, _ = _run(pkg, tmp_path, {"mode": "auth_base"})
    assert proc.returncode == EXIT_AUTH
    assert result == {"status": "auth_required", "message": "some auth problem"}


def test_generic_exception(tmp_path):
    pkg = _make_package(tmp_path)
    proc, result, out = _run(pkg, tmp_path, {"mode": "boom"})
    assert proc.returncode == EXIT_ERROR == 1
    assert result == {
        "status": "error",
        "error_type": "ValueError",
        "message": "could not parse the picks table",
    }
    assert "Traceback (most recent call last)" in proc.stderr
    assert "ValueError: could not parse the picks table" in proc.stderr
    assert not out.exists()


def test_import_failure_is_an_error_result(tmp_path):
    pkg = _make_package(tmp_path, source="import no_such_module_for_quilt_tests\n")
    proc, result, _ = _run(pkg, tmp_path, {})
    assert proc.returncode == EXIT_ERROR
    assert result["status"] == "error"
    assert result["error_type"] == "ModuleNotFoundError"


def test_bad_config_json_is_an_error_result(tmp_path):
    pkg = _make_package(tmp_path)
    proc, result, _ = _run(pkg, tmp_path, "[1, 2]")
    assert proc.returncode == EXIT_ERROR
    assert result["status"] == "error"
    assert "JSON object" in result["message"]


def test_profile_busy(tmp_path, markers):
    pkg = _make_package(tmp_path, auth=AUTH_BLOCK)
    profile = str(tmp_path / "profile")
    with profile_lock(profile, "login"):
        proc, result, out = _run(
            pkg, tmp_path, {"profile_dir": profile, "marker_dir": str(markers)},
        )
    assert proc.returncode == EXIT_PROFILE_BUSY == 3
    assert result["status"] == "profile_busy"
    assert result["holder"]["pid"] == os.getpid()
    assert result["holder"]["role"] == "login"
    assert result["holder"]["started_at"]
    assert "in use by login session" in result["message"]
    # The lock is taken before on_start: the scraper never touched the profile.
    assert not (markers / "started").exists()
    assert not out.exists()


def test_browser_profile_scrape_holds_the_lock_for_the_run(tmp_path):
    pkg = _make_package(tmp_path, auth=AUTH_BLOCK)
    profile = str(tmp_path / "profile")
    proc, result, out = _run(pkg, tmp_path, {"mode": "holder", "profile_dir": profile})
    assert proc.returncode == EXIT_OK, proc.stderr
    assert result == {"status": "ok", "rows": 1}
    lines = out.read_text().splitlines()
    pid, role = lines[1].split(",")
    assert role == "scrape"
    assert int(pid) not in (os.getpid(), 0)
    assert read_profile_holder(profile) is None  # released when the runner exited


def test_relative_profile_dir_resolves_against_the_package(tmp_path):
    pkg = _make_package(tmp_path, auth=AUTH_BLOCK)
    proc, result, out = _run(pkg, tmp_path, {"mode": "holder"})
    assert proc.returncode == EXIT_OK, proc.stderr
    assert out.read_text().splitlines()[1].endswith(",scrape")
    assert (pkg / "profile" / ".quilt-profile.lock").exists()


def test_scraper_without_auth_block_takes_no_lock(tmp_path):
    pkg = _make_package(tmp_path)
    profile = str(tmp_path / "profile")
    with profile_lock(profile, "login"):
        proc, result, _ = _run(pkg, tmp_path, {"profile_dir": profile})
    assert proc.returncode == EXIT_OK, proc.stderr
    assert result == {"status": "ok", "rows": 3}


def test_invalid_auth_block_runs_unlocked_with_a_warning(tmp_path):
    pkg = _make_package(tmp_path, auth={**AUTH_BLOCK, "kind": "api_key"})
    profile = str(tmp_path / "profile")
    with profile_lock(profile, "login"):
        proc, result, _ = _run(pkg, tmp_path, {"profile_dir": profile})
    assert proc.returncode == EXIT_OK, proc.stderr
    assert result == {"status": "ok", "rows": 3}
    assert "ignoring auth: block" in proc.stderr
