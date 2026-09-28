"""sdk/scraper_login.py against a real Chromium (review rev-nimble-bridge, section 13 task C).

A local HTTP site where POST /login sets a session cookie and /picks shows a
table only with that cookie. The helper runs headless (the test-only
--headless override), is driven over stdin exactly like the coordinator
drives it (click, type, submit), and must report `verified`, then
`closed(verified)`, and leave the profile lock free.

Needs a Python with playwright (or patchright), pandas and pyyaml, and that
engine's Chromium: QUILT_LOGIN_TEST_PYTHON names the interpreter, otherwise
the one running the tests is tried. Skipped when none has a Chromium.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs

import pytest
import yaml

from sdk.scraper_auth import profile_lock, read_profile_holder
from sdk.scraper_login import EXIT_DONE, EXIT_NOT_DONE, NOT_SIGNED_IN_MESSAGE, helper_command

pytestmark = pytest.mark.slow

REPO_ROOT = Path(__file__).resolve().parents[2]

_PROBE = textwrap.dedent("""
    import importlib, os, sys
    import pandas, yaml
    engine = sys.argv[1]
    sync_api = importlib.import_module(engine + ".sync_api")
    with sync_api.sync_playwright() as p:
        path = p.chromium.executable_path
    print(path if path and os.path.exists(path) else "")
""")


def _find_browser_python() -> tuple[Optional[str], Optional[str], str]:
    """(python, engine, why-not) for the first interpreter with a usable Chromium."""
    candidates = [os.environ.get("QUILT_LOGIN_TEST_PYTHON"), sys.executable]
    reasons = []
    for python in [c for c in candidates if c]:
        for engine in ("playwright", "patchright"):
            try:
                probe = subprocess.run([python, "-c", _PROBE, engine], capture_output=True,
                                       text=True, timeout=60)
            except (OSError, subprocess.TimeoutExpired) as e:
                reasons.append(f"{python} {engine}: {e}")
                continue
            if probe.returncode == 0 and probe.stdout.strip():
                return python, engine, ""
            reasons.append(f"{python} {engine}: {(probe.stderr.strip().splitlines() or ['no Chromium'])[-1]}")
    return None, None, "; ".join(reasons)


@pytest.fixture(scope="module")
def browser() -> tuple[str, str]:
    """(python, engine) that can run the helper with a real Chromium, or skip."""
    python, engine, why_not = _find_browser_python()
    if python is None:
        pytest.skip(f"no Chromium for the login helper (set QUILT_LOGIN_TEST_PYTHON): {why_not}")
    return python, engine

LOGIN_PAGE = """<!doctype html><html><head><title>Sign in</title>
<style>body{margin:0} #user{position:absolute;left:100px;top:100px;width:200px;height:30px}
#go{position:absolute;left:100px;top:200px;width:100px;height:40px}
#help{position:absolute;left:100px;top:300px;width:100px;height:30px;display:block}</style>
</head><body>
<form method="post" action="/login">
  <input id="user" name="user" autocomplete="off">
  <button id="go" type="submit">Sign in</button>
</form>
<a id="help" href="/help" target="_blank">Help</a>
</body></html>"""

PICKS_PAGE = """<!doctype html><html><head><title>Picks</title></head><body>
<table id="picks"><thead><tr><th>Symbol</th></tr></thead><tbody><tr><td>AAPL</td></tr></tbody></table>
</body></html>"""

HELP_PAGE = "<!doctype html><html><head><title>Help</title></head><body>Help</body></html>"


class _Site(BaseHTTPRequestHandler):
    logins: list = []

    def log_message(self, *args):  # keep test output quiet
        pass

    def _send(self, status: int, body: str = "", headers: Optional[dict] = None) -> None:
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        signed_in = "session=ok" in (self.headers.get("Cookie") or "")
        path = self.path.split("?")[0]
        if path == "/login":
            self._send(200, LOGIN_PAGE)
        elif path == "/picks":
            if signed_in:
                self._send(200, PICKS_PAGE)
            else:
                self._send(302, headers={"Location": "/login"})
        elif path == "/help":
            self._send(200, HELP_PAGE)
        else:
            self._send(404, "not found")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        form = parse_qs(self.rfile.read(length).decode())
        user = (form.get("user") or [""])[0]
        type(self).logins.append(user)
        if user:
            self._send(303, headers={"Location": "/picks", "Set-Cookie": "session=ok; Path=/"})
        else:
            self._send(303, headers={"Location": "/login"})


@pytest.fixture
def site():
    _Site.logins = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", _Site.logins
    finally:
        server.shutdown()
        server.server_close()


SCRAPER_SOURCE = textwrap.dedent("""
    from sdk.scraper import QuiltScraper


    class Scraper(QuiltScraper):
        def browser_launch_options(self):
            return {"viewport": {"width": 800, "height": 600}, "locale": "en-US"}

        def on_run(self):
            raise AssertionError("the login helper must never call on_run")
""")


def make_package(root: Path, base_url: str, engine: str, verify_base: Optional[str] = None) -> Path:
    pkg = root / "pkg"
    pkg.mkdir()
    manifest = {
        "name": "demo-scraper",
        "type": "scraper",
        "schedule": "0 14 * * 1-5",
        "entry_point": "scraper.py",
        "class_name": "Scraper",
        "config": {"parameters": [
            {"name": "profile_dir", "type": "string", "default": str(root / "profile")},
        ]},
        "auth": {
            "kind": "browser_profile",
            "engine": engine,
            "profile_dir_param": "profile_dir",
            "login_url": f"{base_url}/login",
            "verify": {"url": f"{verify_base or base_url}/picks", "selector": ["table#picks"],
                       "timeout_s": 5},
            "session_timeout_s": 120,
        },
    }
    (pkg / "quilt.yaml").write_text(yaml.safe_dump(manifest))
    (pkg / "scraper.py").write_text(SCRAPER_SOURCE)
    return pkg


class Helper:
    """The helper as a child process, driven like the coordinator drives it."""

    def __init__(self, pkg: Path, python: str) -> None:
        argv = helper_command(python, str(pkg), str(REPO_ROOT), {}, headless=True)
        self.proc = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(pkg), env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        )
        self.events: list[dict] = []
        self._queue: "queue.Queue[Optional[dict]]" = queue.Queue()
        self.stderr = b""
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _read(self) -> None:
        for line in self.proc.stdout:
            self._queue.put(json.loads(line))  # every stdout line must be JSON
        self._queue.put(None)

    def _drain_stderr(self) -> None:
        self.stderr = self.proc.stderr.read()

    def send(self, command: dict) -> None:
        self.proc.stdin.write((json.dumps(command) + "\n").encode())
        self.proc.stdin.flush()

    def close_stdin(self) -> None:
        self.proc.stdin.close()

    def expect(self, predicate, timeout: float = 30.0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    f"timed out; got {[e['type'] for e in self.events]}; stderr: {self.stderr[-2000:]!r}"
                )
            try:
                event = self._queue.get(timeout=remaining)
            except queue.Empty:
                continue
            if event is None:
                raise AssertionError(
                    f"helper exited; got {[e['type'] for e in self.events]}; "
                    f"stderr: {self.stderr[-2000:]!r}"
                )
            self.events.append(event)
            if predicate(event):
                return event

    def expect_type(self, kind: str, timeout: float = 30.0) -> dict:
        return self.expect(lambda e: e["type"] == kind, timeout)

    def click(self, x: float, y: float) -> None:
        for action in ("move", "down", "up"):
            self.send({"cmd": "mouse", "action": action, "x": x, "y": y, "button": "left",
                       "click_count": 1})

    def type_keys(self, keys: str) -> None:
        for key in keys:
            self.send({"cmd": "key", "action": "down", "key": key, "code": ""})
            self.send({"cmd": "key", "action": "up", "key": key, "code": ""})

    def wait(self, timeout: float = 60.0) -> int:
        code = self.proc.wait(timeout=timeout)
        while True:  # collect what is left
            event = self._queue.get(timeout=5)
            if event is None:
                return code
            self.events.append(event)

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()


def chromium_on_profile(profile_dir: Path) -> list[int]:
    """PIDs of processes still running Chromium on this user-data-dir."""
    needle = f"--user-data-dir={profile_dir}".encode()
    pids = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as f:
                argv = f.read().split(b"\0")
        except OSError:
            continue
        if needle in argv:
            pids.append(int(entry))
    return pids


def assert_profile_released(profile_dir: Path) -> None:
    assert read_profile_holder(str(profile_dir)) is None
    with profile_lock(str(profile_dir), "scrape"):
        pass
    deadline = time.monotonic() + 10
    while chromium_on_profile(profile_dir) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert chromium_on_profile(profile_dir) == []


def test_sign_in_over_stdin_verifies_and_releases_the_lock(tmp_path, site, browser):
    base_url, logins = site
    pkg = make_package(tmp_path, base_url, browser[1])
    profile = tmp_path / "profile"
    helper = Helper(pkg, browser[0])
    try:
        ready = helper.expect_type("ready")
        assert ready == {"type": "ready", "viewport": {"width": 800, "height": 600}, "headed": False}
        assert read_profile_holder(str(profile))["role"] == "login"

        frame = helper.expect_type("frame")
        assert len(frame["data"]) > 100
        assert frame["metadata"]["deviceWidth"] == 800
        helper.expect(lambda e: e["type"] == "pages" and e["pages"]
                      and e["pages"][0]["url"] == f"{base_url}/login")

        helper.click(150, 115)                 # the user field
        helper.type_keys("jack")
        helper.send({"cmd": "key", "action": "down", "key": "é"})   # not in Playwright's layout
        helper.send({"cmd": "key", "action": "up", "key": "é"})
        helper.send({"cmd": "text", "text": "!"})                   # paste
        helper.click(150, 220)                 # Sign in

        verified = helper.expect_type("verified")
        assert verified["url"] == f"{base_url}/picks"
        closed = helper.expect_type("closed")
        assert closed == {"type": "closed", "reason": "verified"}
        assert helper.wait() == EXIT_DONE
    finally:
        helper.kill()

    assert logins == ["jacké!"]
    kinds = [e["type"] for e in helper.events]
    assert kinds.index("verified") < kinds.index("closed") == len(kinds) - 1
    assert "heartbeat" in kinds
    assert b"jack" not in helper.stderr        # input payloads never reach a log
    assert_profile_released(profile)
    # The session cookie was flushed into the profile, and Chromium kept the
    # password manager off.
    prefs = json.loads((profile / "Default" / "Preferences").read_text())
    assert prefs["credentials_enable_service"] is False
    assert prefs["profile"]["password_manager_enabled"] is False


def test_popup_switch_check_miss_and_cancel(tmp_path, site, browser):
    base_url, _ = site
    pkg = make_package(tmp_path, base_url, browser[1])
    helper = Helper(pkg, browser[0])
    try:
        helper.expect_type("ready")
        helper.expect(lambda e: e["type"] == "pages" and e["pages"]
                      and e["pages"][0]["url"] == f"{base_url}/login")

        helper.click(150, 315)                 # target=_blank link: a second page
        pages = helper.expect(lambda e: e["type"] == "pages" and len(e["pages"]) == 2
                              and e["pages"][1]["url"] == f"{base_url}/help")["pages"]
        assert [p["active"] for p in pages] == [False, True]
        assert pages[1]["id"] != pages[0]["id"]

        helper.send({"cmd": "switch_page", "id": pages[0]["id"]})
        helper.expect(lambda e: e["type"] == "pages" and e["pages"][0]["active"])

        helper.send({"cmd": "check"})          # not signed in: /picks redirects to /login
        helper.expect(lambda e: e["type"] == "status" and e["state"] == "checking")
        miss = helper.expect(lambda e: e["type"] == "status" and e["message"] == NOT_SIGNED_IN_MESSAGE)
        assert miss["state"] == "waiting_for_user"

        helper.send({"cmd": "cancel"})
        assert helper.expect_type("closed") == {"type": "closed", "reason": "cancelled"}
        assert helper.wait() == EXIT_NOT_DONE
    finally:
        helper.kill()
    assert not [e for e in helper.events if e["type"] == "verified"]
    assert_profile_released(tmp_path / "profile")


def test_stdin_eof_closes_the_browser(tmp_path, site, browser):
    base_url, _ = site
    pkg = make_package(tmp_path, base_url, browser[1])
    helper = Helper(pkg, browser[0])
    try:
        helper.expect_type("ready")
        helper.close_stdin()                    # the coordinator went away
        assert helper.expect_type("closed") == {"type": "closed", "reason": "stdin_closed"}
        assert helper.wait() == EXIT_NOT_DONE
    finally:
        helper.kill()
    assert_profile_released(tmp_path / "profile")


def test_screencast_follows_a_cross_site_navigation(tmp_path, site, browser):
    base_url, _ = site
    # 127.0.0.1 and localhost are different sites: Chromium swaps the renderer
    # process on this navigation, and the screencast must keep going.
    other = base_url.replace("127.0.0.1", "localhost")
    pkg = make_package(tmp_path, base_url, browser[1], verify_base=other)
    helper = Helper(pkg, browser[0])
    try:
        helper.expect_type("ready")
        helper.expect_type("frame")
        helper.send({"cmd": "navigate", "target": "verify"})
        # /picks on localhost redirects to its /login (no cookie on that site).
        helper.expect(lambda e: e["type"] == "pages" and e["pages"][0]["url"] == f"{other}/login")
        # Frames arrive only when the page changes: type into the new
        # renderer's page, and its frames must still reach us.
        helper.click(150, 115)
        helper.type_keys("x")
        frame = helper.expect_type("frame")
        assert frame["metadata"]["deviceWidth"] == 800
        helper.send({"cmd": "cancel"})
        assert helper.expect_type("closed") == {"type": "closed", "reason": "cancelled"}
        assert helper.wait() == EXIT_NOT_DONE
    finally:
        helper.kill()
    assert_profile_released(tmp_path / "profile")
