"""Shared set-up for the login session manager and route tests.

The manager runs tests/coordinator/login_fakes/fake_login_helper.py instead
of the real helper, through its `helper_command` hook.
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
import time
from pathlib import Path
from typing import Iterable, Optional
from unittest.mock import MagicMock

from coordinator.services.scraper_engine import ScraperResult
from coordinator.services.scraper_login import LoginSessionManager
from coordinator.services.scraper_registry import AuthState, ScraperRecord, ScraperRegistry
from sdk.scraper_auth import parse_auth

FAKE_HELPER = Path(__file__).resolve().with_name("fake_login_helper.py")
NAME = "alpha-picks-scraper"
OK = ScraperResult(success=True, output_path=None)


def auth_manifest(name: str, *, headless: bool = True) -> dict:
    return {
        "name": name,
        "type": "scraper",
        "schedule": "0 14 * * 1-5",
        "version": "1.0.0",
        "config": {"parameters": [
            {"name": "profile_dir", "type": "string", "default": "~/.cache/unused-profile"},
            {"name": "headless", "type": "bool", "default": headless},
        ]},
        "auth": {
            "kind": "browser_profile",
            "profile_dir_param": "profile_dir",
            "login_url": "https://example.test/login",
            "verify": {"url": "https://example.test/picks", "selector": "table"},
        },
    }


class Env:
    """A registry with scripted scrape results, packages on disk and a manager factory."""

    def __init__(self, tmp_path: Path, results: Iterable[ScraperResult] = ()) -> None:
        self.tmp_path = tmp_path
        self.packages_dir = tmp_path / "packages"
        self.packages_dir.mkdir()
        self.results = list(results)
        self.runs: list[dict] = []
        self.broadcasts: list[dict] = []
        self.engine = MagicMock()
        self.engine.run_scraper = MagicMock(side_effect=self._run_scraper)
        self.registry = ScraperRegistry(
            engine=self.engine,
            scheduler=MagicMock(),
            packages_dir=str(self.packages_dir),
            configs_dir=str(tmp_path / "configs"),
            broadcast=self._broadcast,
        )

    async def _broadcast(self, message: dict) -> None:
        self.broadcasts.append(message)

    def _run_scraper(self, name, fmt, config):
        # Seen from inside the confirmation scrape: was the scraper already ok?
        auth = self.registry._auth_memory.get(name) or AuthState()
        self.runs.append({"name": name, "auth_state": auth.state})
        assert self.results, "engine.run_scraper called more often than the test expects"
        return self.results.pop(0)

    def profile_dir(self, name: str = NAME) -> Path:
        return self.tmp_path / "profiles" / name

    def add_scraper(
        self,
        name: str = NAME,
        *,
        headless: bool = True,
        session_timeout_s: Optional[int] = None,
        auth: bool = True,
    ) -> ScraperRecord:
        (self.packages_dir / name).mkdir(exist_ok=True)
        manifest = auth_manifest(name, headless=headless)
        if not auth:
            manifest.pop("auth")
        parsed = parse_auth(manifest)
        if parsed is not None and session_timeout_s is not None:
            # Below the manifest minimum of 60 s, so the timeout test stays fast.
            parsed = dataclasses.replace(parsed, session_timeout_s=session_timeout_s)
        record = ScraperRecord(
            name=name,
            schedule=manifest["schedule"],
            manifest=manifest,
            config={"profile_dir": str(self.profile_dir(name)), "headless": headless},
            auth=parsed,
        )
        self.registry._scrapers[name] = record
        return record

    def set_needs_login(self, name: str = NAME) -> None:
        self.registry._auth_memory[name] = AuthState(
            state="needs_login", reason="auth_required", message="login wall",
        )

    def auth_state(self, name: str = NAME) -> Optional[str]:
        return (self.registry._auth_memory.get(name) or AuthState()).state

    def manager(self, scenario: str = "interactive", *extra: str, **kwargs) -> LoginSessionManager:
        options = {
            "helper_command": fake_command(scenario, *extra, record=self.record_path),
            "broadcast": self._broadcast,
            "cancel_wait_s": 2.0,
            "term_wait_s": 1.0,
            "reap_grace_s": 0.3,
            "watchdog_interval_s": 0.05,
            "x11_socket_dir": str(self.tmp_path / "no-x11"),
        }
        options.update(kwargs)
        return LoginSessionManager(self.registry, **options)

    @property
    def record_path(self) -> Path:
        return self.tmp_path / "helper-stdin.log"

    def recorded(self) -> list[str]:
        try:
            return self.record_path.read_text().splitlines()
        except FileNotFoundError:
            return []


def fake_command(scenario: str, *extra: str, record: Optional[Path] = None):
    """A helper_command hook that runs the fake helper; the profile comes from the config."""
    def build(python: str, pkg_dir: str, quilt_root: str, config: dict) -> list[str]:
        argv = [sys.executable, str(FAKE_HELPER), "--scenario", scenario,
                "--profile-dir", config.get("profile_dir", "")]
        if record is not None:
            argv += ["--record", str(record)]
        return argv + list(extra)
    return build


async def wait_for(predicate, timeout: float = 10.0, interval: float = 0.02):
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"condition not met within {timeout} s")
        await asyncio.sleep(interval)


async def wait_for_state(manager: LoginSessionManager, name: str, *states: str, timeout: float = 10.0) -> dict:
    def current():
        session = manager.public_session(name)
        return session if session is not None and session["state"] in states else None
    try:
        return await wait_for(current, timeout)
    except AssertionError:
        raise AssertionError(
            f"session for {name} never reached {states}; now {manager.public_session(name)}"
        ) from None


def process_alive(pid: int) -> bool:
    """True while `pid` runs and is not a zombie."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            state = f.read().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return state != "Z"


def kill_quietly(pid: int) -> None:
    try:
        os.kill(pid, 9)
    except OSError:
        pass
