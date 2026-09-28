"""scripts/scraper_login.py: the terminal fallback for Re-login (review rev-nimble-bridge, section 7.7)."""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from sdk.scraper_login import EXIT_DONE, EXIT_NOT_DONE

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "scraper_login.py"
_spec = importlib.util.spec_from_file_location("scraper_login_script", SCRIPT)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)

LoginSetupError = script.LoginSetupError
build_invocation = script.build_invocation
display_env = script.display_env
find_package = script.find_package
load_overrides = script.load_overrides
FAKES_DIR = REPO_ROOT / "tests" / "sdk" / "login_fakes"
PROTECTED = "https://example.test/picks"

SCRAPER_SOURCE = """
from sdk.scraper import QuiltScraper


class Scraper(QuiltScraper):
    def on_run(self):
        raise AssertionError("never called by the login helper")
"""


def make_package(packages: Path, dirname: str, *, name: str | None = None, auth: bool = True,
                 login_url: str = "https://example.test/login") -> Path:
    pkg = packages / dirname
    pkg.mkdir(parents=True)
    manifest = {
        "type": "scraper",
        "schedule": "0 14 * * 1-5",
        "entry_point": "scraper.py",
        "class_name": "Scraper",
        "config": {"parameters": [
            {"name": "profile_dir", "type": "string", "default": "profile"},
        ]},
    }
    if name:
        manifest["name"] = name
    if auth:
        manifest["auth"] = {
            "kind": "browser_profile",
            "engine": "fakeplaywright",
            "profile_dir_param": "profile_dir",
            "login_url": login_url,
            "verify": {"url": PROTECTED, "selector": "table.picks"},
        }
    (pkg / "quilt.yaml").write_text(yaml.safe_dump(manifest))
    (pkg / "scraper.py").write_text(SCRAPER_SOURCE)
    return pkg


def test_find_package_by_directory_or_manifest_name(tmp_path):
    packages = tmp_path / "packages"
    by_dir = make_package(packages, "alpha-picks-scraper")
    by_name = make_package(packages, "checkout-dir", name="renamed-scraper")
    (packages / "broken").mkdir()
    (packages / "broken" / "quilt.yaml").write_text("{not: [yaml")
    assert find_package(packages, "alpha-picks-scraper")[0] == by_dir
    pkg, manifest = find_package(packages, "renamed-scraper")
    assert pkg == by_name and manifest["name"] == "renamed-scraper"
    with pytest.raises(LoginSetupError, match="no scraper package named 'checkout-dir'"):
        find_package(packages, "checkout-dir")   # registered under its manifest name
    with pytest.raises(LoginSetupError):
        find_package(tmp_path / "missing", "x")


def test_load_overrides(tmp_path):
    (tmp_path / "demo.json").write_text(json.dumps({"profile_dir": "~/p", "headless": False}))
    (tmp_path / "bad.json").write_text("{nope")
    assert load_overrides(tmp_path, "demo") == {"profile_dir": "~/p", "headless": False}
    assert load_overrides(tmp_path, "bad") == {}
    assert load_overrides(tmp_path, "absent") == {}


def test_build_invocation_uses_overrides_venv_and_local_mode(tmp_path, monkeypatch):
    packages = tmp_path / "packages"
    configs = tmp_path / "configs"
    configs.mkdir()
    pkg = make_package(packages, "demo")
    (configs / "demo.json").write_text(json.dumps({"profile_dir": "/somewhere/profile"}))
    venv_python = pkg / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("")
    monkeypatch.setenv("DISPLAY", ":7")

    argv, cwd, env = build_invocation("demo", root=tmp_path, packages_dir=packages,
                                      configs_dir=configs)
    assert argv[0] == str(venv_python)
    assert f"sys.path.append({str(tmp_path)!r})" in argv[2]
    assert argv[argv.index("--config-json") + 1] == '{"profile_dir": "/somewhere/profile"}'
    assert argv[argv.index("--mode") + 1] == "local"
    assert "--headless" not in argv
    assert cwd == pkg
    assert env["DISPLAY"] == ":7"


def test_build_invocation_refuses_a_scraper_without_auth(tmp_path):
    packages = tmp_path / "packages"
    make_package(packages, "plain", auth=False)
    with pytest.raises(LoginSetupError, match="no browser-profile login"):
        build_invocation("plain", root=tmp_path, packages_dir=packages, configs_dir=tmp_path)


def test_display_env_finds_an_x_server_or_explains(tmp_path, monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    with pytest.raises(LoginSetupError, match="needs an X display"):
        display_env(str(tmp_path))

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(tmp_path / "X3"))
    sock.listen(1)
    try:
        assert display_env(str(tmp_path))["DISPLAY"] == ":3"
    finally:
        sock.close()


def _run_script(tmp_path: Path, packages: Path, fake: dict, stdin: str = ""):
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(REPO_ROOT), str(FAKES_DIR)]),
        "FAKE_PLAYWRIGHT": json.dumps(fake),
    }
    return subprocess.run(
        [sys.executable, str(SCRIPT), "demo",
         "--packages-dir", str(packages), "--configs-dir", str(tmp_path / "configs"), "--headless"],
        input=stdin, capture_output=True, text=True, env=env, timeout=60,
    )


def test_script_end_to_end_prints_the_confirm_hint(tmp_path):
    packages = tmp_path / "packages"
    make_package(packages, "demo", login_url=PROTECTED)
    proc = _run_script(tmp_path, packages, {"signed_in": True})
    assert proc.returncode == EXIT_DONE, proc.stderr
    assert f"Signed in ({PROTECTED})" in proc.stdout
    assert proc.stdout.rstrip().endswith(
        "Run `quilt data scraper-run demo` to confirm and clear the dashboard banner."
    )


def test_script_cancel_does_not_print_the_hint(tmp_path):
    packages = tmp_path / "packages"
    make_package(packages, "demo")
    # "q" may arrive before the browser is up; the helper still cancels cleanly.
    proc = _run_script(tmp_path, packages, {}, stdin="q\n")
    assert proc.returncode == EXIT_NOT_DONE, proc.stderr
    assert "Login session ended: cancelled." in proc.stdout
    assert "scraper-run" not in proc.stdout


def test_script_reports_setup_errors(tmp_path):
    proc = _run_script(tmp_path, tmp_path / "packages", {})
    assert proc.returncode == 1
    assert "error: no scraper package named 'demo'" in proc.stderr
