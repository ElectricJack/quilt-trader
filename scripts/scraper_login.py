#!/usr/bin/env python3
"""Sign a scraper's browser profile in again from a terminal on the coordinator host.

    python scripts/scraper_login.py alpha-picks-scraper

The dashboard's Re-login button is the usual way. This is the fallback for
when the coordinator is down. It resolves the scraper package, its venv, its
config overrides (data/scraper_configs/<name>.json) and the X display the
same way the coordinator does, then runs the login helper
(sdk/scraper_login.py) in the package's venv with --mode local:

- a Chromium window opens on the profile, at the scraper's login page;
- it finishes by itself once the page shows you are signed in;
- Enter checks now, q + Enter (or Ctrl+C) cancels;
- it also ends when you close the window or the session times out.

It takes the same profile lock as scrapes and dashboard sessions, so it
cannot collide with them. Afterwards, run `quilt data scraper-run <name>` to
confirm and clear the dashboard banner.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from coordinator.services.scraper_engine import find_x_display  # noqa: E402
from sdk.scraper_auth import AuthConfigError, parse_auth  # noqa: E402
from sdk.scraper_login import EXIT_DONE, EXIT_PROFILE_BUSY, helper_command  # noqa: E402


class LoginSetupError(Exception):
    """The helper can't be started; the message says why."""


def find_package(packages_dir: Path, name: str) -> tuple[Path, dict]:
    """The scraper package directory and manifest for a registered name.

    The registry names a scraper by its manifest `name` (falling back to the
    directory name), so look for the directory first and then scan.
    """
    candidates = [packages_dir / name]
    if packages_dir.is_dir():
        candidates += sorted(p for p in packages_dir.iterdir() if p.is_dir() and p.name != name)
    for pkg_dir in candidates:
        manifest_path = pkg_dir / "quilt.yaml"
        if not manifest_path.is_file():
            continue
        try:
            manifest = yaml.safe_load(manifest_path.read_text()) or {}
        except Exception:  # noqa: BLE001 — a broken neighbour must not stop the lookup
            continue
        if manifest.get("type") == "scraper" and (manifest.get("name") or pkg_dir.name) == name:
            return pkg_dir, manifest
    raise LoginSetupError(f"no scraper package named {name!r} under {packages_dir}")


def load_overrides(configs_dir: Path, name: str) -> dict:
    """Config overrides, exactly as ScraperRegistry._load_overrides reads them."""
    path = configs_dir / f"{name}.json"
    if not path.is_file():
        return {}
    try:
        overrides = json.loads(path.read_text())
    except Exception as e:  # noqa: BLE001
        print(f"warning: ignoring {path}: {e}", file=sys.stderr)
        return {}
    return overrides if isinstance(overrides, dict) else {}


def venv_python(pkg_dir: Path) -> str:
    """The package venv's interpreter, falling back to this one like ScraperEngine."""
    candidate = pkg_dir / ".venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


def display_env(x11_socket_dir: Optional[str] = None) -> dict:
    """Environment with an X display for the headed login window."""
    if os.environ.get("DISPLAY"):
        return dict(os.environ)
    display = find_x_display(x11_socket_dir) if x11_socket_dir else find_x_display()
    if display is None:
        raise LoginSetupError(
            "the login window needs an X display, but DISPLAY is unset and no X server "
            "answers under /tmp/.X11-unix. Run this from a desktop session (WSLg) or "
            "under xvfb-run with a way to see it."
        )
    return {**os.environ, "DISPLAY": display}


def build_invocation(
    name: str,
    *,
    root: Path = ROOT,
    packages_dir: Optional[Path] = None,
    configs_dir: Optional[Path] = None,
    x11_socket_dir: Optional[str] = None,
    headless: bool = False,
) -> tuple[list[str], Path, dict]:
    """(argv, cwd, env) that run the login helper in local mode for `name`."""
    packages_dir = packages_dir or root / "packages"
    configs_dir = configs_dir or root / "data" / "scraper_configs"
    pkg_dir, manifest = find_package(packages_dir, name)
    try:
        auth = parse_auth(manifest)
    except AuthConfigError as e:
        raise LoginSetupError(f"scraper {name} has an invalid auth: block: {e}") from None
    if auth is None or auth.kind != "browser_profile":
        raise LoginSetupError(
            f"scraper {name} declares no browser-profile login (no auth: block in its quilt.yaml)"
        )
    config = load_overrides(configs_dir, name)
    env = dict(os.environ) if headless else display_env(x11_socket_dir)
    argv = helper_command(
        venv_python(pkg_dir), str(pkg_dir), str(root), config, mode="local", headless=headless,
    )
    return argv, pkg_dir, env


def run_helper(argv: list[str], cwd: Path, env: dict) -> int:
    """Run the helper in the foreground and wait for it.

    Ctrl+C reaches the helper too (same process group); it cancels and closes
    the browser properly, so keep waiting instead of killing it.
    """
    with subprocess.Popen(argv, cwd=str(cwd), env=env) as proc:
        while True:
            try:
                return proc.wait()
            except KeyboardInterrupt:
                continue


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sign a scraper's browser profile in again (terminal fallback for Re-login).",
    )
    parser.add_argument("name", help="the scraper's name, e.g. alpha-picks-scraper")
    parser.add_argument("--packages-dir", type=Path, help="default: <quilt root>/packages")
    parser.add_argument("--configs-dir", type=Path, help="default: <quilt root>/data/scraper_configs")
    parser.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)  # tests only
    args = parser.parse_args(argv)

    try:
        helper_argv, cwd, env = build_invocation(
            args.name, packages_dir=args.packages_dir, configs_dir=args.configs_dir,
            headless=args.headless,
        )
    except LoginSetupError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    code = run_helper(helper_argv, cwd, env)
    if code == EXIT_DONE:
        print(f"Run `quilt data scraper-run {args.name}` to confirm and clear the dashboard banner.")
    elif code == EXIT_PROFILE_BUSY:
        print("The profile is in use; wait for that process to finish, then try again.",
              file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
