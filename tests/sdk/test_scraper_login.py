"""sdk/scraper_login.py: the login helper (review rev-nimble-bridge, sections 7.2, 7.5, 7.7).

Unit tests: command validation, key mapping, URL matching, page tracking and
the Preferences edit. Session tests drive LoginSession in-process against a
scripted fake engine (tests/sdk/login_fakes/fakeplaywright), and a few run
the helper as a child process, as the coordinator and the terminal fallback
do. The real-Chromium flow is in test_scraper_login_integration.py (slow).
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
import yaml

from sdk.scraper_auth import (
    ScraperAuth,
    ScraperAuthVerify,
    profile_lock,
    read_profile_holder,
)
from sdk.scraper_login import (
    EXIT_DONE,
    EXIT_ERROR,
    EXIT_NOT_DONE,
    EXIT_PROFILE_BUSY,
    MAX_COMMAND_BYTES,
    NOT_SIGNED_IN_MESSAGE,
    PASSWORD_MANAGER_PREFS,
    Command,
    CommandError,
    LocalOutput,
    LoginSession,
    PageTracker,
    ProtocolOutput,
    SessionPlan,
    classify_key,
    disable_password_manager,
    exit_code,
    helper_command,
    parse_command,
    url_matches,
)
from tests.sdk.login_fakes.fakeplaywright.async_api import FakeEngine

REPO_ROOT = Path(__file__).resolve().parents[2]
FAKES_DIR = Path(__file__).resolve().parent / "login_fakes"

LOGIN_URL = "https://example.test/login"
PROTECTED = "https://example.test/picks"


# ---------------------------------------------------------------------------
# Command validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("line, expected", [
    ({"cmd": "mouse", "action": "move", "x": 10, "y": 20.5},
     Command("mouse", {"action": "move", "x": 10.0, "y": 20.5, "button": "left", "click_count": 1})),
    ({"cmd": "mouse", "action": "down", "x": 1, "y": 2, "button": "right", "click_count": 2},
     Command("mouse", {"action": "down", "x": 1.0, "y": 2.0, "button": "right", "click_count": 2})),
    ({"cmd": "wheel", "x": 5, "y": 6, "dx": 0, "dy": -120},
     Command("wheel", {"x": 5.0, "y": 6.0, "dx": 0.0, "dy": -120.0})),
    ({"cmd": "key", "action": "down", "key": "a", "code": "KeyA"},
     Command("key", {"action": "down", "key": "a", "code": "KeyA"})),
    ({"cmd": "key", "action": "up", "key": "é"},
     Command("key", {"action": "up", "key": "é", "code": None})),
    # Soft keyboards (Android) report an empty code.
    ({"cmd": "key", "action": "down", "key": "j", "code": ""},
     Command("key", {"action": "down", "key": "j", "code": ""})),
    ({"cmd": "text", "text": "x" * 256}, Command("text", {"text": "x" * 256})),
    ({"cmd": "switch_page", "id": "p2"}, Command("switch_page", {"id": "p2"})),
    ({"cmd": "history", "action": "reload"}, Command("history", {"action": "reload"})),
    ({"cmd": "navigate", "target": "verify"}, Command("navigate", {"target": "verify"})),
    ({"cmd": "check"}, Command("check")),
    ({"cmd": "cancel", "extra": "ignored"}, Command("cancel")),
])
def test_parse_command_accepts(line, expected):
    assert parse_command(json.dumps(line)) == expected
    assert parse_command(json.dumps(line).encode()) == expected


@pytest.mark.parametrize("line, reason", [
    ("not json", "not valid JSON"),
    ("[1, 2]", "not a JSON object"),
    ('{"cmd": "eval", "js": "alert(1)"}', "unknown cmd"),
    ('{"action": "move"}', "unknown cmd"),
    ('{"cmd": "mouse", "action": "drag", "x": 1, "y": 1}', "action must be one of"),
    ('{"cmd": "mouse", "action": "move", "y": 1}', "missing field 'x'"),
    ('{"cmd": "mouse", "action": "move", "x": "1", "y": 1}', "x must be a finite number"),
    ('{"cmd": "mouse", "action": "move", "x": true, "y": 1}', "x must be a finite number"),
    ('{"cmd": "mouse", "action": "move", "x": 1e308, "y": 1}', "x is out of range"),
    ('{"cmd": "mouse", "action": "move", "x": NaN, "y": 1}', "x must be a finite number"),
    ('{"cmd": "mouse", "action": "down", "x": 1, "y": 1, "button": "back"}', "button must be one of"),
    ('{"cmd": "mouse", "action": "down", "x": 1, "y": 1, "click_count": 9}', "click_count"),
    ('{"cmd": "wheel", "x": 1, "y": 1, "dx": 0}', "missing field 'dy'"),
    ('{"cmd": "key", "action": "press", "key": "a"}', "action must be one of"),
    ('{"cmd": "key", "action": "down", "key": ""}', "key must be a non-empty string"),
    ('{"cmd": "key", "action": "down", "key": "' + "k" * 40 + '"}', "key is longer than"),
    ('{"cmd": "key", "action": "down", "key": "a", "code": 7}', "code must be a non-empty string"),
    ('{"cmd": "text", "text": "' + "x" * 257 + '"}', "text is longer than 256"),
    ('{"cmd": "text"}', "text must be a non-empty string"),
    ('{"cmd": "switch_page"}', "id must be a non-empty string"),
    ('{"cmd": "history", "action": "home"}', "action must be one of"),
    ('{"cmd": "navigate", "target": "https://evil.test"}', "target must be one of"),
    ('{"cmd": "navigate", "url": "https://evil.test"}', "target must be one of"),
])
def test_parse_command_rejects(line, reason):
    with pytest.raises(CommandError, match=reason):
        parse_command(line)


def test_rejection_messages_never_echo_the_payload():
    secret = "hunter2-secret"
    for line in (
        json.dumps({"cmd": "text", "text": secret * 30}),
        json.dumps({"cmd": "key", "action": "down", "key": secret * 3}),
        json.dumps({"cmd": secret}),
        secret,
    ):
        with pytest.raises(CommandError) as info:
            parse_command(line)
        assert secret not in str(info.value)


# ---------------------------------------------------------------------------
# Key mapping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", [
    "a", "Z", "0", " ", "!", "@", "~", "\\", "'", '"',
    "Enter", "Tab", "Backspace", "Delete", "Escape", "Shift", "Control", "Alt", "Meta",
    "AltGraph", "CapsLock", "ArrowLeft", "ArrowDown", "Home", "End", "PageUp", "F1", "F12",
    "ContextMenu", "MediaPlayPause",
])
def test_classify_key_layout_keys(key):
    assert classify_key(key) == "key"


@pytest.mark.parametrize("key", [
    "\u00e9", "\u00df", "\u20ac", "\u00f1", "\u03a9", "\u4e2d", "\U0001f600",
    "\U0001f44d\U0001f3fd",                      # thumbs up + skin tone
    "\U0001f1eb\U0001f1f7",                      # flag
    "\U0001f468\u200d\U0001f469\u200d\U0001f467",  # ZWJ family
    "e\u0301",                                    # e + combining acute
])
def test_classify_key_non_layout_characters_are_text(key):
    assert classify_key(key) == "text"


@pytest.mark.parametrize("key", [
    "Dead", "Unidentified", "Process", "Compose", "F13", "F24", "MediaStop", "Clear",
    "BrowserBack", "Fn", "\n", "\t", "\x1b", "\x7f", "\u200b", "\x85",
])
def test_classify_key_ignores_unknown_named_keys_and_controls(key):
    assert classify_key(key) is None


# ---------------------------------------------------------------------------
# URL matching
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url, expected", [
    ("https://seekingalpha.com/alpha-picks/picks/current",
     "https://seekingalpha.com/alpha-picks/picks/current"),
    ("https://seekingalpha.com/alpha-picks/picks/current/",
     "https://seekingalpha.com/alpha-picks/picks/current"),
    ("https://seekingalpha.com/alpha-picks/picks/current?tab=1#top",
     "https://seekingalpha.com/alpha-picks/picks/current/"),
    ("HTTPS://SeekingAlpha.com/alpha-picks/picks/current",
     "https://seekingalpha.com/alpha-picks/picks/current"),
    ("https://example.test:443/a", "https://example.test/a"),
    ("http://127.0.0.1:8123/picks", "http://127.0.0.1:8123/picks"),
    ("https://example.test/", "https://example.test"),
    ("https://example.test/a/b/current", "https://example.test/*/current"),
    ("https://example.test/a/b/current?x=1", "https://example.test/*/current"),
    ("https://example.test/a/b/current#frag", "https://example.test/*/current"),
    ("https://sub.example.test/x", "https://*.example.test/*"),
])
def test_url_matches(url, expected):
    assert url_matches(url, expected)


@pytest.mark.parametrize("url, expected", [
    ("http://seekingalpha.com/alpha-picks/picks/current",
     "https://seekingalpha.com/alpha-picks/picks/current"),          # scheme
    ("https://www.seekingalpha.com/alpha-picks/picks/current",
     "https://seekingalpha.com/alpha-picks/picks/current"),          # host
    ("https://seekingalpha.com/login?next=/alpha-picks/picks/current",
     "https://seekingalpha.com/alpha-picks/picks/current"),          # path; query ignored
    ("https://seekingalpha.com/alpha-picks/picks/current/extra",
     "https://seekingalpha.com/alpha-picks/picks/current"),
    ("https://example.test:8443/a", "https://example.test/a"),       # port
    ("about:blank", "https://example.test/a"),
    ("chrome-error://chromewebdata/", "https://example.test/a"),
    ("", "https://example.test/a"),
    ("https://example.test/a/b/current?next=/current", "https://example.test/*/current/x"),
    ("https://example.test/login?next=/a/current", "https://example.test/*/current"),
])
def test_url_does_not_match(url, expected):
    assert not url_matches(url, expected)


# ---------------------------------------------------------------------------
# Page tracking
# ---------------------------------------------------------------------------

def test_newest_page_becomes_active():
    tracker = PageTracker()
    assert tracker.opened("p1") == "p1"
    assert tracker.opened("p2") == "p2"
    assert tracker.active == "p2"
    assert tracker.ids == ["p1", "p2"]


def test_closing_the_active_page_hands_over_to_the_most_recent_open_page():
    tracker = PageTracker()
    for page_id in ("p1", "p2", "p3"):
        tracker.opened(page_id)
    assert tracker.switch("p2")
    assert tracker.closed("p2") == "p3"   # most recently opened still open
    assert tracker.closed("p3") == "p1"
    assert tracker.closed("p1") is None
    assert tracker.ids == []


def test_closing_an_inactive_page_keeps_the_active_one():
    tracker = PageTracker()
    for page_id in ("p1", "p2", "p3"):
        tracker.opened(page_id)
    tracker.switch("p1")
    assert tracker.closed("p3") == "p1"
    assert tracker.ids == ["p1", "p2"]


def test_switch_to_unknown_page_is_refused():
    tracker = PageTracker()
    tracker.opened("p1")
    assert not tracker.switch("p9")
    assert tracker.active == "p1"
    assert tracker.closed("p9") == "p1"  # closing an unknown page changes nothing


def test_reopening_a_known_id_does_not_duplicate_it():
    tracker = PageTracker()
    tracker.opened("p1")
    tracker.opened("p2")
    tracker.opened("p1")
    assert tracker.ids == ["p1", "p2"]
    assert tracker.active == "p1"


# ---------------------------------------------------------------------------
# Password manager Preferences
# ---------------------------------------------------------------------------

def test_preferences_created_when_missing(tmp_path):
    path = disable_password_manager(str(tmp_path / "profile"))
    assert json.loads(Path(path).read_text()) == PASSWORD_MANAGER_PREFS
    assert Path(path) == tmp_path / "profile" / "Default" / "Preferences"


def test_preferences_merged_into_existing_settings(tmp_path):
    prefs_path = tmp_path / "Default" / "Preferences"
    prefs_path.parent.mkdir(parents=True)
    prefs_path.write_text(json.dumps({
        "credentials_enable_service": True,
        "profile": {"password_manager_enabled": True, "name": "Person 1", "exit_type": "Normal"},
        "browser": {"window_placement": {"left": 10}},
    }))
    disable_password_manager(str(tmp_path))
    prefs = json.loads(prefs_path.read_text())
    assert prefs == {
        "credentials_enable_service": False,
        "profile": {"password_manager_enabled": False, "name": "Person 1", "exit_type": "Normal"},
        "browser": {"window_placement": {"left": 10}},
    }
    assert not list(prefs_path.parent.glob(".Preferences-*"))


def test_unreadable_preferences_are_left_alone(tmp_path):
    prefs_path = tmp_path / "Default" / "Preferences"
    prefs_path.parent.mkdir(parents=True)
    prefs_path.write_text("{not json")
    with pytest.raises(ValueError):
        disable_password_manager(str(tmp_path))
    assert prefs_path.read_text() == "{not json"


# ---------------------------------------------------------------------------
# Command line and output plumbing
# ---------------------------------------------------------------------------

def test_helper_command_mirrors_the_runner_bootstrap():
    argv = helper_command("/venv/bin/python", "/pkgs/demo", "/quilt", {"profile_dir": "~/p"})
    assert argv[0] == "/venv/bin/python" and argv[1] == "-c"
    assert "sys.path.insert(0, '/pkgs/demo')" in argv[2]
    assert "sys.path.append('/quilt')" in argv[2]
    assert "from sdk.scraper_login import main" in argv[2]
    assert argv[3:] == ["--pkg-dir", "/pkgs/demo", "--config-json", '{"profile_dir": "~/p"}',
                        "--mode", "protocol"]
    local = helper_command("py", "/p", "/q", mode="local", headless=True)
    assert local[-3:] == ["--mode", "local", "--headless"]
    assert local[5:7] == ["--config-json", "{}"]
    with pytest.raises(ValueError):
        helper_command("py", "/p", "/q", mode="vnc")


def test_exit_codes():
    assert exit_code("verified") == exit_code("browser_closed") == EXIT_DONE == 0
    assert exit_code("cancelled") == exit_code("timeout") == exit_code("stdin_closed") == EXIT_NOT_DONE
    assert exit_code("error") == EXIT_ERROR


def test_protocol_output_writes_lines_in_order_then_calls_back(tmp_path):
    read_fd, write_fd = os.pipe()
    out = ProtocolOutput(write_fd)
    written = []
    out.send({"type": "heartbeat"})
    out.send({"type": "frame", "data": "x" * 200_000}, lambda: written.append(True))
    out.send({"type": "closed", "reason": "cancelled"})
    chunks = []
    while True:
        chunk = os.read(read_fd, 1 << 20)
        chunks.append(chunk)
        if b'"closed"' in b"".join(chunks):
            break
    out.close()
    os.close(write_fd)
    lines = b"".join(chunks).decode().splitlines()
    assert [json.loads(line)["type"] for line in lines] == ["heartbeat", "frame", "closed"]
    assert written == [True]
    os.close(read_fd)


def test_protocol_output_survives_a_dead_reader():
    read_fd, write_fd = os.pipe()
    os.close(read_fd)
    out = ProtocolOutput(write_fd)
    called = []
    out.send({"type": "heartbeat"}, lambda: called.append(1))
    out.close()
    assert called == [1]
    os.close(write_fd)


def test_local_output_prints_status_lines(capsys):
    import io
    stream = io.StringIO()
    out = LocalOutput(stream, session_timeout_s=1200)
    out.send({"type": "ready", "viewport": {"width": 1, "height": 1}, "headed": True})
    out.send({"type": "pages", "pages": [{"id": "p1", "url": "u", "title": "Sign in", "active": True}]})
    out.send({"type": "pages", "pages": [{"id": "p1", "url": "u2", "title": "Again", "active": True}]})
    out.send({"type": "status", "state": "waiting_for_user", "message": NOT_SIGNED_IN_MESSAGE})
    out.send({"type": "verified", "url": PROTECTED})
    out.send({"type": "closed", "reason": "verified"})
    text = stream.getvalue()
    assert "A Chromium window is open" in text and "20 min" in text
    assert "Press Enter to check now, or q + Enter to cancel." in text
    assert text.count("Active tab:") == 1          # only when the active tab changes
    assert NOT_SIGNED_IN_MESSAGE in text
    assert f"Signed in ({PROTECTED})" in text
    assert "Login session ended: verified." in text
    assert "{" not in text                          # no JSON in local mode


# ---------------------------------------------------------------------------
# LoginSession against the fake engine (in-process)
# ---------------------------------------------------------------------------

class RecordingOutput:
    local = False

    def __init__(self, engine: FakeEngine | None = None, *, hold_frames: bool = False) -> None:
        self.events: list[dict] = []
        self.engine = engine
        self.hold_frames = hold_frames
        self.held: list = []

    def send(self, event, on_written=None):
        self.events.append(event)
        if self.engine is not None:
            self.engine.site.record("out", event["type"])
        if on_written is not None:
            if self.hold_frames:
                self.held.append(on_written)
            else:
                on_written()

    def close(self, timeout=5.0):
        pass

    def of(self, kind):
        return [e for e in self.events if e["type"] == kind]

    def statuses(self):
        return [e["message"] for e in self.of("status")]

    async def wait_for(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                raise AssertionError(f"timed out; events: {[e['type'] for e in self.events]}")
            await asyncio.sleep(0.01)


def make_auth(*, verify_url=PROTECTED, selectors=("table.picks",), session_timeout_s=60,
              login_url=LOGIN_URL, verify_timeout_s=2) -> ScraperAuth:
    return ScraperAuth(
        kind="browser_profile",
        profile_dir_param="profile_dir",
        login_url=login_url,
        verify=ScraperAuthVerify(url=verify_url, selectors=tuple(selectors), timeout_s=verify_timeout_s),
        engine="fakeplaywright",
        session_timeout_s=session_timeout_s,
    )


def make_session(tmp_path, engine, out=None, *, mode="protocol", headed=False,
                 launch_options=None, stdin_fd=None, **auth_kwargs):
    plan = SessionPlan(
        auth=make_auth(**auth_kwargs),
        profile_dir=str(tmp_path / "profile"),
        launch_options=launch_options if launch_options is not None else {"locale": "en-US"},
        headed=headed,
        async_api=engine,
        mode=mode,
    )
    session = LoginSession(plan, out or RecordingOutput(engine), stdin_fd)
    session.passive_check_interval_s = 0.05
    session.verified_settle_s = 0.0
    session.heartbeat_interval_s = 0.05
    return session


async def started(session):
    task = asyncio.create_task(session.run())
    await session._out.wait_for(lambda: session._out.of("ready") or task.done())
    return task


def calls(engine, name):
    return [c for c in engine.site.calls if c[0] == name]


def cmd(line: dict) -> Command:
    return parse_command(json.dumps(line))


@pytest.mark.asyncio
async def test_passive_check_verifies_closes_and_releases_the_lock(tmp_path):
    engine = FakeEngine({"signed_in": True})
    session = make_session(tmp_path, engine, login_url=PROTECTED, headed=True)
    out = session._out
    reason, code = await asyncio.wait_for(session.run(), 10)

    assert (reason, code) == ("verified", EXIT_DONE)
    kinds = [e["type"] for e in out.events if e["type"] != "heartbeat"]
    assert kinds.index("ready") < kinds.index("verified")
    assert out.of("ready")[0] == {"type": "ready", "viewport": {"width": 800, "height": 600},
                                  "headed": True}
    assert out.of("verified")[0]["url"] == PROTECTED
    assert out.of("heartbeat")
    # Launched on the profile, headed, with the scraper's own options.
    assert calls(engine, "launch") == [["launch", str(tmp_path / "profile"), False, {"locale": "en-US"}]]
    assert calls(engine, "ctx.close")
    # Nothing navigated to verify.url: the passive check never navigates.
    assert [c[2] for c in calls(engine, "page.goto")] == [PROTECTED]
    prefs = json.loads((tmp_path / "profile" / "Default" / "Preferences").read_text())
    assert prefs["credentials_enable_service"] is False
    assert read_profile_holder(str(tmp_path / "profile")) is None


@pytest.mark.asyncio
async def test_lock_is_held_as_login_during_the_session(tmp_path):
    engine = FakeEngine()
    session = make_session(tmp_path, engine)
    task = await started(session)
    holder = read_profile_holder(str(tmp_path / "profile"))
    assert holder["role"] == "login" and holder["pid"] == os.getpid()
    session.handle_command(cmd({"cmd": "cancel"}))
    assert await asyncio.wait_for(task, 5) == ("cancelled", EXIT_NOT_DONE)
    assert read_profile_holder(str(tmp_path / "profile")) is None


@pytest.mark.asyncio
async def test_local_mode_takes_the_login_local_role_and_skips_screencast(tmp_path):
    engine = FakeEngine()
    out = RecordingOutput(engine)
    session = make_session(tmp_path, engine, out, mode="local")
    task = await started(session)
    assert read_profile_holder(str(tmp_path / "profile"))["role"] == "login-local"
    session._handle_local_line("q\n")
    assert await asyncio.wait_for(task, 5) == ("cancelled", EXIT_NOT_DONE)
    assert not out.of("heartbeat") and not out.of("frame")
    assert not calls(engine, "cdp.send")


@pytest.mark.asyncio
async def test_typed_sign_in_over_the_protocol_verifies(tmp_path):
    engine = FakeEngine()
    session = make_session(tmp_path, engine)
    out = session._out
    task = await started(session)
    await out.wait_for(lambda: out.of("frame"))
    session.handle_command(cmd({"cmd": "mouse", "action": "down", "x": 100, "y": 200}))
    session.handle_command(cmd({"cmd": "mouse", "action": "up", "x": 100, "y": 200}))
    for ch in "let":
        session.handle_command(cmd({"cmd": "key", "action": "down", "key": ch, "code": f"Key{ch.upper()}"}))
        session.handle_command(cmd({"cmd": "key", "action": "up", "key": ch}))
    session.handle_command(cmd({"cmd": "text", "text": "mein"}))
    session.handle_command(cmd({"cmd": "key", "action": "down", "key": "Enter"}))
    session.handle_command(cmd({"cmd": "key", "action": "up", "key": "Enter"}))
    assert await asyncio.wait_for(task, 10) == ("verified", EXIT_DONE)
    assert out.of("verified")[0]["url"] == PROTECTED
    assert ["mouse.down", "page1", "left", 1] in engine.site.calls
    assert ["key.insert_text", "page1", "mein"] in engine.site.calls


@pytest.mark.asyncio
async def test_key_mapping_applied_to_the_page(tmp_path):
    engine = FakeEngine()
    session = make_session(tmp_path, engine)
    task = await started(session)
    for action in ("down", "up"):
        for key in ("é", "Dead", "a", "Shift", "😀", "Unidentified"):
            session.handle_command(cmd({"cmd": "key", "action": action, "key": key}))
    session.handle_command(cmd({"cmd": "wheel", "x": 10, "y": 10, "dx": 0, "dy": 240}))
    await session._out.wait_for(lambda: calls(engine, "mouse.wheel"))
    session.handle_command(cmd({"cmd": "cancel"}))
    await asyncio.wait_for(task, 5)
    keys = [c[0:1] + c[2:] for c in engine.site.calls if c[0].startswith("key.")]
    assert keys == [
        ["key.insert_text", "é"], ["key.down", "a"], ["key.down", "Shift"],
        ["key.insert_text", "😀"],
        ["key.up", "a"], ["key.up", "Shift"],
    ]


@pytest.mark.asyncio
async def test_coordinates_are_clamped_to_the_viewport(tmp_path):
    engine = FakeEngine()
    session = make_session(tmp_path, engine)
    task = await started(session)
    session.handle_command(cmd({"cmd": "mouse", "action": "move", "x": 5000, "y": -40}))
    session.handle_command(cmd({"cmd": "mouse", "action": "move", "x": 12.5, "y": 599.9}))
    await session._out.wait_for(lambda: len(calls(engine, "mouse.move")) == 2)
    session.handle_command(cmd({"cmd": "cancel"}))
    await asyncio.wait_for(task, 5)
    assert calls(engine, "mouse.move") == [
        ["mouse.move", "page1", 799, 0], ["mouse.move", "page1", 12.5, 599],
    ]


@pytest.mark.asyncio
async def test_frames_are_acked_only_after_they_are_written(tmp_path):
    engine = FakeEngine()
    out = RecordingOutput(engine, hold_frames=True)
    session = make_session(tmp_path, engine, out)
    task = await started(session)
    await out.wait_for(lambda: out.of("frame"))
    frame = out.of("frame")[0]
    assert frame["data"] == "ZmFrZS1qcGVn"
    assert frame["metadata"] == {"deviceWidth": 800, "deviceHeight": 600, "pageScaleFactor": 1,
                                 "offsetTop": 0, "scrollOffsetX": 0, "scrollOffsetY": 0,
                                 "timestamp": 1.5}
    start = [c for c in calls(engine, "cdp.send") if c[2] == "Page.startScreencast"]
    assert start == [["cdp.send", "page1", "Page.startScreencast",
                      {"format": "jpeg", "quality": 70, "maxWidth": 1600, "maxHeight": 900}]]

    def acks():
        return [c for c in calls(engine, "cdp.send") if c[2] == "Page.screencastFrameAck"]

    await asyncio.sleep(0.1)
    assert acks() == []                       # not written yet -> not acked
    out.held.pop(0)()                         # the writer finished the line
    await out.wait_for(lambda: acks())
    assert acks()[0][3] == {"sessionId": 1}
    session.handle_command(cmd({"cmd": "cancel"}))
    await asyncio.wait_for(task, 5)


@pytest.mark.asyncio
async def test_popup_becomes_active_and_switching_moves_the_screencast(tmp_path):
    engine = FakeEngine()
    session = make_session(tmp_path, engine)
    out = session._out
    task = await started(session)

    def last_pages():
        return out.of("pages")[-1]["pages"] if out.of("pages") else []

    def screencasts():
        return [(c[1], c[2]) for c in calls(engine, "cdp.send")
                if c[2] in ("Page.startScreencast", "Page.stopScreencast")]

    await out.wait_for(lambda: last_pages() and last_pages()[0]["url"] == LOGIN_URL)
    assert last_pages() == [{"id": "p1", "url": LOGIN_URL, "title": f"Title of {LOGIN_URL}",
                             "active": True}]

    session.handle_command(cmd({"cmd": "text", "text": "popup"}))
    session.handle_command(cmd({"cmd": "key", "action": "down", "key": "Enter"}))
    await out.wait_for(lambda: len(last_pages()) == 2 and last_pages()[1]["active"])
    await out.wait_for(lambda: screencasts()[-1] == ("page2", "Page.startScreencast"))
    assert screencasts() == [("page1", "Page.startScreencast"), ("page1", "Page.stopScreencast"),
                             ("page2", "Page.startScreencast")]

    session.handle_command(cmd({"cmd": "switch_page", "id": "p1"}))
    await out.wait_for(lambda: last_pages()[0]["active"])
    await out.wait_for(lambda: screencasts()[-1] == ("page1", "Page.startScreencast"))

    session.handle_command(cmd({"cmd": "switch_page", "id": "p9"}))
    await out.wait_for(lambda: "That tab is no longer open." in out.statuses())

    # Input follows the active page.
    session.handle_command(cmd({"cmd": "mouse", "action": "move", "x": 1, "y": 1}))
    await out.wait_for(lambda: calls(engine, "mouse.move"))
    assert calls(engine, "mouse.move")[-1][1] == "page1"

    # The active popup closes itself: the most recent page still open (p1) takes over.
    session.handle_command(cmd({"cmd": "switch_page", "id": "p2"}))
    await out.wait_for(lambda: last_pages()[1]["active"])
    await engine.context.pages[1].close()
    await out.wait_for(lambda: len(last_pages()) == 1 and last_pages()[0]["active"])
    await out.wait_for(lambda: screencasts()[-1] == ("page1", "Page.startScreencast"))

    session.handle_command(cmd({"cmd": "cancel"}))
    assert (await asyncio.wait_for(task, 5))[0] == "cancelled"


@pytest.mark.asyncio
async def test_closing_every_page_is_browser_closed(tmp_path):
    engine = FakeEngine()
    session = make_session(tmp_path, engine)
    task = await started(session)
    await engine.context.pages[0].close()
    assert await asyncio.wait_for(task, 5) == ("browser_closed", EXIT_DONE)
    assert not session._out.of("verified")
    assert read_profile_holder(str(tmp_path / "profile")) is None


@pytest.mark.asyncio
async def test_dialogs(tmp_path):
    engine = FakeEngine()
    session = make_session(tmp_path, engine)
    out = session._out
    task = await started(session)
    page = engine.context.pages[0]
    leave = page.open_dialog("beforeunload", "")
    alert = page.open_dialog("alert", "Please enable cookies")
    confirm = page.open_dialog("confirm", "Stay signed in?")
    await out.wait_for(lambda: len([s for s in out.statuses() if "dialog" in s]) == 3)
    assert (leave.handled, alert.handled, confirm.handled) == ("accepted", "dismissed", "dismissed")
    assert "Accepted a beforeunload dialog" in out.statuses()
    assert "Dismissed a alert dialog: Please enable cookies" in out.statuses()
    session.handle_command(cmd({"cmd": "cancel"}))
    await asyncio.wait_for(task, 5)


@pytest.mark.asyncio
async def test_check_miss_keeps_the_session_open(tmp_path):
    engine = FakeEngine()
    session = make_session(tmp_path, engine)
    out = session._out
    task = await started(session)
    session.handle_command(cmd({"cmd": "check"}))
    await out.wait_for(lambda: NOT_SIGNED_IN_MESSAGE in out.statuses(), timeout=10)
    states = [e["state"] for e in out.of("status")]
    assert "checking" in states and states[-1] == "waiting_for_user"
    assert [c[2] for c in calls(engine, "page.goto")] == [LOGIN_URL, PROTECTED]
    assert not task.done()
    session.handle_command(cmd({"cmd": "cancel"}))
    assert (await asyncio.wait_for(task, 5))[0] == "cancelled"


@pytest.mark.asyncio
async def test_check_hit_verifies(tmp_path):
    engine = FakeEngine({"signed_in": True})
    session = make_session(tmp_path, engine)
    session.passive_check_interval_s = 60   # only the active check can see it
    task = await started(session)
    session.handle_command(cmd({"cmd": "check"}))
    assert await asyncio.wait_for(task, 10) == ("verified", EXIT_DONE)
    assert session._out.of("verified")[0]["url"] == PROTECTED


@pytest.mark.asyncio
async def test_check_needs_verify_url(tmp_path):
    engine = FakeEngine({"signed_in": True})
    session = make_session(tmp_path, engine, verify_url=None)
    session.passive_check_interval_s = 60
    task = await started(session)
    session.handle_command(cmd({"cmd": "check"}))
    session.handle_command(cmd({"cmd": "navigate", "target": "verify"}))
    await session._out.wait_for(lambda: len(session._out.of("status")) >= 3)
    assert any("verify.url" in s for s in session._out.statuses())
    assert "This scraper has no verify URL to go to." in session._out.statuses()
    session.handle_command(cmd({"cmd": "cancel"}))
    await asyncio.wait_for(task, 5)


@pytest.mark.asyncio
async def test_selector_only_verify_matches_on_any_url(tmp_path):
    engine = FakeEngine({"signed_in": True})
    session = make_session(tmp_path, engine, verify_url=None, selectors=("!!bad", "table.picks"),
                           login_url=PROTECTED + "/current")
    assert await asyncio.wait_for(session.run(), 10) == ("verified", EXIT_DONE)


@pytest.mark.asyncio
async def test_url_must_match_even_when_the_selector_is_visible(tmp_path):
    engine = FakeEngine({"signed_in": True})
    session = make_session(tmp_path, engine, login_url=PROTECTED + "/other")
    task = await started(session)
    await asyncio.sleep(0.3)
    assert not task.done()
    session.handle_command(cmd({"cmd": "navigate", "target": "verify"}))
    assert await asyncio.wait_for(task, 5) == ("verified", EXIT_DONE)


@pytest.mark.asyncio
async def test_history_and_navigate_commands(tmp_path):
    engine = FakeEngine()
    session = make_session(tmp_path, engine)
    task = await started(session)
    session.handle_command(cmd({"cmd": "history", "action": "reload"}))
    session.handle_command(cmd({"cmd": "history", "action": "forward"}))
    session.handle_command(cmd({"cmd": "navigate", "target": "login"}))
    await session._out.wait_for(lambda: len(calls(engine, "page.goto")) == 2)
    session.handle_command(cmd({"cmd": "history", "action": "back"}))
    await session._out.wait_for(lambda: calls(engine, "page.go_back"))
    session.handle_command(cmd({"cmd": "cancel"}))
    await asyncio.wait_for(task, 5)
    assert calls(engine, "page.reload") and calls(engine, "page.go_forward")


@pytest.mark.asyncio
async def test_login_page_that_fails_to_load_is_tolerated(tmp_path):
    engine = FakeEngine()
    session = make_session(tmp_path, engine, login_url="https://unreachable.test/login")
    task = await started(session)
    assert session._out.of("ready")
    await session._out.wait_for(lambda: any("did not finish loading" in s for s in session._out.statuses()))
    session.handle_command(cmd({"cmd": "cancel"}))
    assert (await asyncio.wait_for(task, 5))[0] == "cancelled"


@pytest.mark.asyncio
async def test_session_timeout(tmp_path):
    engine = FakeEngine()
    session = make_session(tmp_path, engine, session_timeout_s=0.3)
    reason, code = await asyncio.wait_for(session.run(), 5)
    assert (reason, code) == ("timeout", EXIT_NOT_DONE)
    assert calls(engine, "ctx.close")
    assert read_profile_holder(str(tmp_path / "profile")) is None


@pytest.mark.asyncio
async def test_invalid_and_oversize_lines_get_a_status_and_nothing_else(tmp_path):
    engine = FakeEngine()
    session = make_session(tmp_path, engine)
    task = await started(session)
    session._handle_protocol_line(b'{"cmd": "text", "text": "' + b"s" * 300 + b'"}\n')
    session._handle_protocol_line(b"garbage\n")
    session._handle_protocol_line(None)
    session._handle_protocol_line(b"\n")
    await session._out.wait_for(lambda: len(session._out.of("status")) >= 4)
    statuses = session._out.statuses()
    assert "Ignored an invalid command: text is longer than 256 characters." in statuses
    assert "Ignored an invalid command: not valid JSON." in statuses
    assert f"Ignored a command longer than {MAX_COMMAND_BYTES} bytes." in statuses
    assert not calls(engine, "key.insert_text")
    session.handle_command(cmd({"cmd": "cancel"}))
    await asyncio.wait_for(task, 5)


@pytest.mark.asyncio
async def test_stdin_eof_ends_the_session(tmp_path):
    engine = FakeEngine()
    read_fd, write_fd = os.pipe()
    session = make_session(tmp_path, engine, stdin_fd=read_fd)
    task = await started(session)
    os.write(write_fd, json.dumps({"cmd": "mouse", "action": "move", "x": 3, "y": 4}).encode() + b"\n")
    os.write(write_fd, b'{"cmd": "text", "text": "' + b"z" * (MAX_COMMAND_BYTES + 10) + b'"}\n')
    await session._out.wait_for(lambda: calls(engine, "mouse.move"))
    os.close(write_fd)
    assert await asyncio.wait_for(task, 5) == ("stdin_closed", EXIT_NOT_DONE)
    assert f"Ignored a command longer than {MAX_COMMAND_BYTES} bytes." in session._out.statuses()
    assert calls(engine, "ctx.close")
    os.close(read_fd)


@pytest.mark.asyncio
async def test_profile_busy(tmp_path):
    engine = FakeEngine()
    session = make_session(tmp_path, engine)
    with profile_lock(str(tmp_path / "profile"), "scrape"):
        reason, code = await asyncio.wait_for(session.run(), 5)
    assert (reason, code) == ("error", EXIT_PROFILE_BUSY)
    error = session._out.of("error")[0]
    assert error["kind"] == "profile_busy"
    assert error["holder"]["role"] == "scrape" and error["holder"]["pid"] == os.getpid()
    assert "in use by scrape" in error["message"]
    assert not calls(engine, "launch")


@pytest.mark.asyncio
async def test_launch_failure(tmp_path):
    engine = FakeEngine({"launch_error": "Target page, context or browser has been closed"})
    session = make_session(tmp_path, engine)
    reason, code = await asyncio.wait_for(session.run(), 5)
    assert (reason, code) == ("error", EXIT_ERROR)
    error = session._out.of("error")[0]
    assert error["kind"] == "launch_failed"
    assert "has been closed" in error["message"]
    assert read_profile_holder(str(tmp_path / "profile")) is None


# ---------------------------------------------------------------------------
# The helper as a child process
# ---------------------------------------------------------------------------

SCRAPER_SOURCE = textwrap.dedent("""
    from sdk.scraper import QuiltScraper


    class Scraper(QuiltScraper):
        def on_start(self, config):
            print("NOISE from on_start")          # must not reach the protocol stream
            self.config = config

        def browser_launch_options(self):
            return {"locale": self.config.get("locale", "en-US"), "headless": False,
                    "user_data_dir": "/elsewhere"}

        def on_run(self):
            raise AssertionError("the login helper must never call on_run")
""")


def make_package(root: Path, *, engine: str = "fakeplaywright", verify_url: str | None = PROTECTED,
                 login_url: str = LOGIN_URL) -> Path:
    pkg = root / "pkg"
    pkg.mkdir()
    verify: dict = {"selector": ["table.picks"]}
    if verify_url:
        verify["url"] = verify_url
    manifest = {
        "name": "demo-scraper",
        "type": "scraper",
        "schedule": "0 14 * * 1-5",
        "entry_point": "scraper.py",
        "class_name": "Scraper",
        "config": {"parameters": [
            {"name": "profile_dir", "type": "string", "default": "profile"},
            {"name": "headless", "type": "bool", "default": True},
        ]},
        "auth": {
            "kind": "browser_profile",
            "engine": engine,
            "profile_dir_param": "profile_dir",
            "login_url": login_url,
            "verify": verify,
            "session_timeout_s": 60,
        },
    }
    (pkg / "quilt.yaml").write_text(yaml.safe_dump(manifest))
    (pkg / "scraper.py").write_text(SCRAPER_SOURCE)
    return pkg


def helper_env(tmp_path: Path, fake: dict | None = None) -> dict:
    return {
        **os.environ,
        # This checkout's sdk ahead of any installed copy, plus the fake engine.
        "PYTHONPATH": os.pathsep.join([str(REPO_ROOT), str(FAKES_DIR)]),
        "FAKE_PLAYWRIGHT": json.dumps({"log": str(tmp_path / "fake.log"), **(fake or {})}),
    }


def fake_log(tmp_path: Path) -> list:
    path = tmp_path / "fake.log"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def run_helper(pkg: Path, tmp_path: Path, stdin_lines: list, *, fake: dict | None = None,
               mode: str = "protocol", config: dict | None = None, timeout: float = 60):
    argv = helper_command(sys.executable, str(pkg), str(REPO_ROOT), config or {}, mode=mode)
    stdin = "".join((line if isinstance(line, str) else json.dumps(line)) + "\n" for line in stdin_lines)
    return subprocess.run(
        argv, input=stdin, capture_output=True, text=True, cwd=str(pkg),
        env=helper_env(tmp_path, fake), timeout=timeout,
    )


def events(stdout: str) -> list[dict]:
    # Every stdout line must be a JSON event: nothing else may reach the stream.
    return [json.loads(line) for line in stdout.splitlines()]


def test_protocol_child_signs_in_and_keeps_stdout_clean(tmp_path):
    pkg = make_package(tmp_path)
    argv = helper_command(sys.executable, str(pkg), str(REPO_ROOT), {"locale": "de-DE"})
    proc = subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=str(pkg), env=helper_env(tmp_path), text=True,
    )
    seen = [json.loads(proc.stdout.readline())]
    while seen[-1]["type"] == "heartbeat":             # heartbeats start before launch
        seen.append(json.loads(proc.stdout.readline()))
    assert seen[-1] == {"type": "ready", "viewport": {"width": 800, "height": 600}, "headed": False}
    for line in ({"cmd": "text", "text": "letmein"}, {"cmd": "key", "action": "down", "key": "Enter"}):
        proc.stdin.write(json.dumps(line) + "\n")
        proc.stdin.flush()
    # Keep stdin open until the helper verifies: EOF would mean the coordinator died.
    while seen[-1]["type"] != "verified":
        line = proc.stdout.readline()
        assert line, proc.stderr.read()
        seen.append(json.loads(line))
    stdout, stderr = proc.communicate(timeout=60)
    assert proc.returncode == EXIT_DONE, stderr
    got = seen + events(stdout)
    kinds = [e["type"] for e in got]
    assert "verified" in kinds and "frame" in kinds and "pages" in kinds and "heartbeat" in kinds
    assert got[-1] == {"type": "closed", "reason": "verified"}
    assert "NOISE from on_start" in stderr and "NOISE" not in stdout
    assert "letmein" not in stderr                       # input payloads are never logged
    launch = [c for c in fake_log(tmp_path) if c[0] == "launch"][0]
    # headless=true in the manifest -> headless; the helper owns user_data_dir/headless.
    assert launch == ["launch", str(pkg / "profile"), True, {"locale": "de-DE"}]
    assert read_profile_holder(str(pkg / "profile")) is None


def test_protocol_child_exits_on_stdin_eof(tmp_path):
    pkg = make_package(tmp_path)
    proc = run_helper(pkg, tmp_path, [])
    assert proc.returncode == EXIT_NOT_DONE, proc.stderr
    got = events(proc.stdout)
    assert got[-1] == {"type": "closed", "reason": "stdin_closed"}
    assert any(c[0] == "ctx.close" for c in fake_log(tmp_path))
    assert read_profile_holder(str(pkg / "profile")) is None


def test_protocol_child_reports_a_missing_engine(tmp_path):
    pkg = make_package(tmp_path, engine="no_such_engine")
    proc = run_helper(pkg, tmp_path, [])
    assert proc.returncode == EXIT_ERROR
    got = events(proc.stdout)
    assert [e["type"] for e in got] == ["error", "closed"]
    assert got[0]["kind"] == "engine_missing"
    assert "no_such_engine.async_api" in got[0]["message"]
    assert "pip install no_such_engine" in got[0]["message"]
    assert got[1] == {"type": "closed", "reason": "error"}


def test_protocol_child_reports_profile_busy(tmp_path):
    pkg = make_package(tmp_path)
    with profile_lock(str(pkg / "profile"), "login-local"):
        proc = run_helper(pkg, tmp_path, [])
    assert proc.returncode == EXIT_PROFILE_BUSY
    got = events(proc.stdout)
    assert [e["type"] for e in got if e["type"] != "heartbeat"] == ["error", "closed"]
    error = next(e for e in got if e["type"] == "error")
    assert error["kind"] == "profile_busy" and error["holder"]["role"] == "login-local"


def test_protocol_child_without_auth_block(tmp_path):
    pkg = make_package(tmp_path)
    manifest = yaml.safe_load((pkg / "quilt.yaml").read_text())
    del manifest["auth"]
    (pkg / "quilt.yaml").write_text(yaml.safe_dump(manifest))
    proc = run_helper(pkg, tmp_path, [])
    assert proc.returncode == EXIT_ERROR
    got = events(proc.stdout)
    assert got[0]["kind"] == "internal" and "no auth: block" in got[0]["message"]


@pytest.mark.parametrize("config_json, message", [
    ("{nope", "--config-json is not valid JSON"),
    ("[1]", "--config-json must be a JSON object"),
])
def test_protocol_child_rejects_bad_config_json(tmp_path, config_json, message):
    pkg = make_package(tmp_path)
    argv = helper_command(sys.executable, str(pkg), str(REPO_ROOT), {})
    argv[argv.index("--config-json") + 1] = config_json
    proc = subprocess.run(argv, input="", capture_output=True, text=True, cwd=str(pkg),
                          env=helper_env(tmp_path), timeout=60)
    assert proc.returncode == EXIT_ERROR
    got = events(proc.stdout)
    assert got[0]["kind"] == "internal" and message in got[0]["message"]
    assert got[-1] == {"type": "closed", "reason": "error"}


def test_local_child_prints_status_lines_and_cancels_on_q(tmp_path):
    pkg = make_package(tmp_path)
    argv = helper_command(sys.executable, str(pkg), str(REPO_ROOT), {}, mode="local")
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, cwd=str(pkg),
                            env=helper_env(tmp_path))
    seen = []
    while not any("Press Enter to check now" in line for line in seen):
        line = proc.stdout.readline()
        assert line, proc.stderr.read()
        seen.append(line)
    proc.stdin.write("\nhelp\nq\n")
    proc.stdin.flush()
    stdout, stderr = proc.communicate(timeout=60)
    stdout = "".join(seen) + stdout
    assert proc.returncode == EXIT_NOT_DONE, stderr
    assert "A Chromium window is open" in stdout
    assert "Checking whether you are signed in" in stdout     # Enter = check now
    assert "Login session ended: cancelled." in stdout
    assert '"type"' not in stdout
    launch = [c for c in fake_log(tmp_path) if c[0] == "launch"][0]
    assert launch[2] is False                             # local mode is always headed


def test_local_child_finishes_on_the_passive_check(tmp_path):
    pkg = make_package(tmp_path, login_url=PROTECTED)
    argv = helper_command(sys.executable, str(pkg), str(REPO_ROOT), {}, mode="local")
    # stdin stays open (a terminal): only the passive check can end this.
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, cwd=str(pkg),
                            env=helper_env(tmp_path, {"signed_in": True}))
    stdout, stderr = proc.communicate(timeout=60)  # communicate() closes stdin; local mode ignores EOF
    assert proc.returncode == EXIT_DONE, stderr
    assert f"Signed in ({PROTECTED})" in stdout
    assert "Login session ended: verified." in stdout
