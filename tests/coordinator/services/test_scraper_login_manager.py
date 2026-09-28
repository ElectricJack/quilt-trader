"""LoginSessionManager (review rev-nimble-bridge, sections 7.3-7.5 and 11).

A fake helper script (tests/coordinator/login_fakes/fake_login_helper.py)
stands in for sdk/scraper_login.py and speaks the same JSON-lines protocol,
so these tests drive real subprocesses through every state transition, the
watchdog, the stop sequence, the straggler reap and the confirmation scrape.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys

import pytest

from coordinator.services.scraper_engine import ScraperResult
from coordinator.services.scraper_login import (
    STREAM_LIMIT,
    LoginStartError,
    LoginViewer,
    find_profile_processes,
    parse_viewer_command,
)
from tests.coordinator.login_fakes import (
    NAME,
    OK,
    Env,
    kill_quietly,
    process_alive,
    wait_for,
    wait_for_state,
)

AUTH_REQUIRED = ScraperResult(success=False, error="login wall at /picks", error_kind="auth_required")
PLAIN_ERROR = ScraperResult(success=False, error="KeyError: 'symbol'", error_kind="error")


def _states(env: Env, name: str = NAME) -> list[str]:
    return [
        m["state"] for m in env.broadcasts
        if m.get("type") == "scraper_login_state" and m.get("name") == name
    ]


async def _stop_all(manager) -> None:
    await asyncio.wait_for(manager.shutdown(), 30)


# --- state transitions ---------------------------------------------------------


@pytest.mark.asyncio
async def test_start_reaches_waiting_for_user_and_cancel_ends_it(tmp_path):
    env = Env(tmp_path)
    env.add_scraper()
    manager = env.manager()
    try:
        session, created = await manager.start(NAME)
        assert created is True
        assert session["state"] == "starting"
        assert set(session) == {"id", "state", "message", "started_at", "expires_at", "can_check"}
        assert len(session["id"]) >= 21  # token_urlsafe(16)
        assert env.registry.login_active(NAME)

        waiting = await wait_for_state(manager, NAME, "waiting_for_user")
        assert waiting["message"] == "Sign in in the browser."
        # A scheduled run during the session is refused without spawning.
        result = await env.registry.run(NAME, trigger="schedule")
        assert result.error_kind == "login_in_progress"

        await manager.cancel(NAME)
        ended = await wait_for_state(manager, NAME, "cancelled")
        assert ended["message"] == "The login session was cancelled."
        assert not env.registry.login_active(NAME)
        assert env.recorded() == ['{"cmd":"cancel"}']
        live = manager.get(NAME)
        assert live.process.returncode == 2
        assert _states(env) == ["starting", "waiting_for_user", "cancelled"]
        assert env.engine.run_scraper.call_count == 0
    finally:
        await _stop_all(manager)


@pytest.mark.asyncio
async def test_second_start_returns_the_active_session(tmp_path):
    env = Env(tmp_path)
    env.add_scraper()
    manager = env.manager()
    try:
        first, created = await manager.start(NAME)
        again, created_again = await manager.start(NAME)
        assert created is True and created_again is False
        assert again["id"] == first["id"]
    finally:
        await _stop_all(manager)


@pytest.mark.asyncio
async def test_check_goes_to_checking_and_back(tmp_path):
    env = Env(tmp_path)
    env.add_scraper()
    manager = env.manager()
    try:
        await manager.start(NAME)
        await wait_for_state(manager, NAME, "waiting_for_user")
        await manager.check(NAME)
        await wait_for(lambda: '{"cmd":"check"}' in env.recorded())
        back = await wait_for(
            lambda: (s := manager.public_session(NAME))["message"].startswith("Not signed in") and s
        )
        assert back["state"] == "waiting_for_user"
        assert "checking" in _states(env)
    finally:
        await _stop_all(manager)


# --- confirmation after verified / browser_closed ---------------------------------


@pytest.mark.asyncio
async def test_verified_marks_login_verified_before_the_confirmation_scrape(tmp_path):
    csv = tmp_path / "out.csv"
    csv.write_text("symbol,company\nAAPL,Apple\nMSFT,Microsoft\nNVDA,Nvidia\n")
    env = Env(tmp_path, [ScraperResult(success=True, output_path=str(csv))])
    env.add_scraper()
    env.set_needs_login()
    manager = env.manager("verify")
    try:
        await manager.start(NAME)
        ended = await wait_for_state(manager, NAME, "succeeded", "confirm_failed", "failed")
        assert ended["state"] == "succeeded"
        assert ended["message"] == "Signed in and scraped 3 rows."
        # mark_login_verified ran before the scrape: it already saw auth ok.
        assert env.runs == [{"name": NAME, "auth_state": None}]
        assert env.auth_state() is None
        assert _states(env) == ["starting", "waiting_for_user", "verified", "confirming", "succeeded"]
        assert not env.registry.login_active(NAME)
        changed = [m for m in env.broadcasts if m["type"] == "scraper_auth_changed"]
        assert changed == [{"type": "scraper_auth_changed", "name": NAME, "auth_state": "ok"}]
    finally:
        await _stop_all(manager)


@pytest.mark.asyncio
async def test_confirmation_failure_after_verified_keeps_auth_ok(tmp_path):
    env = Env(tmp_path, [PLAIN_ERROR])
    env.add_scraper()
    env.set_needs_login()
    manager = env.manager("verify")
    try:
        await manager.start(NAME)
        ended = await wait_for_state(manager, NAME, "succeeded", "confirm_failed", "failed")
        assert ended["state"] == "confirm_failed"
        assert ended["message"] == "The confirmation scrape failed: KeyError: 'symbol'"
        assert env.auth_state() is None
        assert env.registry.get(NAME).last_error == "KeyError: 'symbol'"
    finally:
        await _stop_all(manager)


@pytest.mark.asyncio
async def test_confirmation_auth_error_after_verified_is_back_to_needs_login(tmp_path):
    env = Env(tmp_path, [AUTH_REQUIRED])
    env.add_scraper()
    env.set_needs_login()
    manager = env.manager("verify")
    try:
        await manager.start(NAME)
        ended = await wait_for_state(manager, NAME, "succeeded", "confirm_failed", "failed")
        assert ended["state"] == "confirm_failed"
        assert ended["message"].startswith(
            "Sign-in was verified, but the confirmation scrape still hit a login wall"
        )
        assert env.auth_state() == "needs_login"
    finally:
        await _stop_all(manager)


@pytest.mark.asyncio
async def test_browser_closed_runs_one_confirmation_scrape_without_marking_verified(tmp_path):
    env = Env(tmp_path, [OK])
    env.add_scraper()
    env.set_needs_login()
    manager = env.manager("browser_closed")
    try:
        await manager.start(NAME)
        ended = await wait_for_state(manager, NAME, "succeeded", "confirm_failed", "failed")
        assert ended["state"] == "succeeded"
        # No mark_login_verified: the scrape itself still saw needs_login, and
        # its success is what cleared it.
        assert env.runs == [{"name": NAME, "auth_state": "needs_login"}]
        assert env.auth_state() is None
        assert env.engine.run_scraper.call_count == 1
        assert _states(env) == [
            "starting", "waiting_for_user", "browser_closed", "confirming", "succeeded",
        ]
    finally:
        await _stop_all(manager)


@pytest.mark.asyncio
async def test_browser_closed_confirmation_auth_error_stays_paused(tmp_path):
    env = Env(tmp_path, [AUTH_REQUIRED])
    env.add_scraper()
    env.set_needs_login()
    manager = env.manager("browser_closed")
    try:
        await manager.start(NAME)
        ended = await wait_for_state(manager, NAME, "succeeded", "confirm_failed", "failed")
        assert ended["state"] == "confirm_failed"
        assert ended["message"].startswith("The browser was closed, but the confirmation scrape")
        assert env.auth_state() == "needs_login"
        assert env.engine.run_scraper.call_count == 1
    finally:
        await _stop_all(manager)


# --- failures -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_helper_error_fails_the_session_without_a_scrape(tmp_path):
    env = Env(tmp_path)
    env.add_scraper()
    manager = env.manager("engine_missing")
    try:
        await manager.start(NAME)
        ended = await wait_for_state(manager, NAME, "failed")
        assert "patchright is not installed" in ended["message"]
        assert env.engine.run_scraper.call_count == 0
        assert not env.registry.login_active(NAME)
    finally:
        await _stop_all(manager)


@pytest.mark.asyncio
async def test_helper_crash_reports_exit_code_and_stderr_tail(tmp_path):
    env = Env(tmp_path)
    env.add_scraper()
    manager = env.manager("crash")
    try:
        await manager.start(NAME)
        ended = await wait_for_state(manager, NAME, "failed")
        assert "exit code 1" in ended["message"]
        assert "RuntimeError: boom" in ended["message"]
    finally:
        await _stop_all(manager)


@pytest.mark.asyncio
async def test_heartbeat_watchdog_kills_a_hung_helper(tmp_path):
    env = Env(tmp_path)
    env.add_scraper()
    # The hung helper ignores cancel and SIGTERM, so all three stop steps run.
    manager = env.manager("hang", heartbeat_timeout_s=0.5, cancel_wait_s=0.3, term_wait_s=0.3)
    try:
        await manager.start(NAME)
        ended = await wait_for_state(manager, NAME, "failed", timeout=15)
        assert ended["message"].startswith("The login helper stopped responding (no heartbeat")
        assert manager.get(NAME).process.returncode == -signal.SIGKILL
        assert '{"cmd":"cancel"}' in env.recorded()   # step 1 came first
        assert not env.registry.login_active(NAME)
    finally:
        await _stop_all(manager)


@pytest.mark.asyncio
async def test_session_timeout_cancels_the_helper(tmp_path):
    env = Env(tmp_path)
    env.add_scraper(session_timeout_s=1)
    manager = env.manager()
    try:
        await manager.start(NAME)
        ended = await wait_for_state(manager, NAME, "timed_out", timeout=15)
        assert "limit" in ended["message"]
        assert env.recorded() == ['{"cmd":"cancel"}']
        assert manager.get(NAME).process.returncode == 2
        assert env.engine.run_scraper.call_count == 0
    finally:
        await _stop_all(manager)


# --- straggler reap -----------------------------------------------------------------


def test_find_profile_processes_matches_user_data_dir(tmp_path):
    profile = tmp_path / "profile"
    other = tmp_path / "profile-other"
    procs = [
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", arg])
        for arg in (f"--user-data-dir={profile}", f"--user-data-dir={other}")
    ]
    try:
        assert find_profile_processes(str(profile)) == {procs[0].pid}
        assert find_profile_processes(str(profile) + "/") == {procs[0].pid}
    finally:
        for proc in procs:
            proc.kill()
            proc.wait()
    assert find_profile_processes(str(profile)) == set()


@pytest.mark.asyncio
async def test_straggler_browser_is_reaped_after_the_helper_exits(tmp_path):
    env = Env(tmp_path)
    env.add_scraper()
    pid_file = tmp_path / "sleeper.pid"
    manager = env.manager("straggler", "--pid-file", str(pid_file))
    sleeper = None
    try:
        await manager.start(NAME)
        await wait_for_state(manager, NAME, "waiting_for_user")
        sleeper = int(pid_file.read_text())
        assert process_alive(sleeper)
        # It runs in its own session, as Chromium does under Playwright.
        assert os.getpgid(sleeper) != manager.get(NAME).process.pid

        await manager.cancel(NAME)
        await wait_for_state(manager, NAME, "cancelled")
        # The reap happens before the session ends, so it is gone already.
        await wait_for(lambda: not process_alive(sleeper), timeout=3)
    finally:
        if sleeper is not None:
            kill_quietly(sleeper)
        await _stop_all(manager)


# --- frames, viewers and input --------------------------------------------------------


@pytest.mark.asyncio
async def test_a_1_mib_frame_line_reaches_viewers(tmp_path):
    env = Env(tmp_path)
    env.add_scraper()
    frame_bytes = 1024 * 1024  # base64 makes the JSON line ~1.4 MiB
    manager = env.manager("interactive", "--frame-bytes", str(frame_bytes))
    try:
        await manager.start(NAME)
        await wait_for_state(manager, NAME, "waiting_for_user")
        live = manager.get(NAME)
        await wait_for(lambda: live.latest_frame and live.latest_pages)
        frame = json.loads(live.latest_frame)
        assert frame["type"] == "frame"
        assert len(frame["data"]) >= frame_bytes * 4 // 3
        # Far over asyncio's 64 KiB default line limit, well under ours.
        assert 1024 * 1024 < len(live.latest_frame) < STREAM_LIMIT

        viewer = manager.attach_viewer(live)
        first = json.loads(viewer.pending())
        assert first["type"] == "session" and first["session"]["id"] == live.id
        rest = [json.loads(viewer.pending()) for _ in range(2)]
        assert [m["type"] for m in rest] == ["pages", "frame"]
        assert rest[1]["data"] == frame["data"]
        assert viewer.pending() is None
    finally:
        await _stop_all(manager)


@pytest.mark.asyncio
async def test_viewer_mailbox_keeps_only_the_latest_frame():
    viewer = LoginViewer()
    viewer.post('{"type":"session","n":1}')
    for n in range(5):
        viewer.post_frame(f'{{"type":"frame","n":{n}}}')
    viewer.post_pages('{"type":"pages","n":1}')
    viewer.post_pages('{"type":"pages","n":2}')
    viewer.post('{"type":"session","n":2}')
    sent = []
    while (text := viewer.pending()) is not None:
        sent.append(json.loads(text))
    assert sent == [
        {"type": "session", "n": 1},
        {"type": "session", "n": 2},
        {"type": "pages", "n": 2},
        {"type": "frame", "n": 4},
    ]
    viewer.post_frame('{"type":"frame","n":5}')
    viewer.post_ended('{"type":"ended"}')
    viewer.post_frame('{"type":"frame","n":6}')
    assert json.loads(await asyncio.wait_for(viewer.next(), 1)) == {"type": "ended"}
    assert viewer.finished
    assert await asyncio.wait_for(viewer.next(), 1) is None


@pytest.mark.parametrize("message", [
    {"cmd": "mouse", "action": "down", "x": 10, "y": 20.5},
    {"cmd": "mouse", "action": "up", "x": 0, "y": 0, "button": "right", "click_count": 2},
    {"cmd": "wheel", "x": 1, "y": 2, "dx": 0, "dy": -120},
    {"cmd": "key", "action": "down", "key": "Enter", "code": "Enter"},
    {"cmd": "key", "action": "up", "key": "é"},
    {"cmd": "text", "text": "x" * 256},
    {"cmd": "switch_page", "id": "p2"},
    {"cmd": "history", "action": "reload"},
    {"cmd": "navigate", "target": "verify"},
    {"cmd": "check"},
    {"cmd": "cancel"},
])
def test_parse_viewer_command_accepts_the_protocol(message):
    parsed = parse_viewer_command(json.dumps(message))
    assert parsed is not None
    assert parsed["cmd"] == message["cmd"]
    for key, value in message.items():
        assert parsed[key] == value


@pytest.mark.parametrize("raw", [
    "not json",
    "[1, 2]",
    json.dumps({"cmd": "evaluate", "script": "alert(1)"}),
    json.dumps({"cmd": "navigate", "target": "https://evil.test/"}),
    json.dumps({"cmd": "mouse", "action": "down", "x": 1e9, "y": 0}),
    json.dumps({"cmd": "mouse", "action": "down", "x": True, "y": 0}),
    json.dumps({"cmd": "mouse", "action": "down", "x": "5", "y": 0}),
    json.dumps({"cmd": "mouse", "action": "down", "x": 1, "y": 1, "click_count": 4}),
    json.dumps({"cmd": "mouse", "action": "drag", "x": 1, "y": 1}),
    json.dumps({"cmd": "wheel", "x": 1, "y": 1, "dx": 0, "dy": 1e6}),
    json.dumps({"cmd": "text", "text": "x" * 257}),
    json.dumps({"cmd": "text", "text": ""}),
    json.dumps({"cmd": "key", "action": "down", "key": "k" * 33}),
    json.dumps({"cmd": "switch_page", "id": "p" * 33}),
    '{"cmd":"text","text":"' + "x" * 9000 + '"}',
])
def test_parse_viewer_command_drops_invalid_input(raw):
    assert parse_viewer_command(raw) is None


def test_parse_viewer_command_strips_unknown_fields():
    parsed = parse_viewer_command(json.dumps({"cmd": "check", "url": "https://evil.test/"}))
    assert parsed == {"cmd": "check"}


@pytest.mark.asyncio
async def test_viewer_input_reaches_the_helper_and_is_never_logged(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    secret = "hunter2-correct-horse"
    env = Env(tmp_path)
    env.add_scraper()
    manager = env.manager()
    try:
        await manager.start(NAME)
        await wait_for_state(manager, NAME, "waiting_for_user")
        live = manager.get(NAME)
        await manager.handle_viewer_input(live, json.dumps({"cmd": "text", "text": secret}))
        await manager.handle_viewer_input(
            live, json.dumps({"cmd": "text", "text": secret * 20}),  # oversize: dropped
        )
        await manager.handle_viewer_input(live, json.dumps({"cmd": "key", "action": "down", "key": secret}))
        await manager.handle_viewer_input(live, "{" + secret)  # not JSON: dropped
        await wait_for(lambda: len(env.recorded()) >= 1)
        await manager.handle_viewer_input(live, json.dumps({"cmd": "cancel"}))
        await wait_for_state(manager, NAME, "cancelled")

        assert env.recorded() == [
            json.dumps({"cmd": "text", "text": secret}, separators=(",", ":")),
            json.dumps({"cmd": "key", "action": "down", "key": secret}, separators=(",", ":")),
            '{"cmd":"cancel"}',
        ]
        # The helper echoed the input to its stderr; the manager kept it only
        # in memory and logged none of it.
        assert any(secret in line for line in live.stderr_tail)
        assert caplog.records, "expected the manager to log its lifecycle"
        assert secret not in caplog.text
        assert all(secret not in str(r.args) for r in caplog.records)
    finally:
        await _stop_all(manager)


# --- start checks, retention, shutdown ------------------------------------------------


@pytest.mark.asyncio
async def test_start_refusals_leave_no_login_flag(tmp_path):
    env = Env(tmp_path)
    env.add_scraper()
    env.add_scraper("no-auth", auth=False)
    manager = env.manager()
    with pytest.raises(LoginStartError) as e:
        await manager.start("missing")
    assert e.value.status_code == 404
    with pytest.raises(LoginStartError) as e:
        await manager.start("no-auth")
    assert e.value.status_code == 422
    env.registry.get(NAME).last_status = "running"
    with pytest.raises(LoginStartError) as e:
        await manager.start(NAME)
    assert e.value.status_code == 409
    assert not env.registry.login_active(NAME)
    assert manager.get(NAME) is None


@pytest.mark.asyncio
async def test_ended_session_stays_visible_for_the_recent_window(tmp_path):
    env = Env(tmp_path)
    env.add_scraper()
    manager = env.manager("engine_missing", recent_s=0.2)
    try:
        started, _ = await manager.start(NAME)
        await wait_for_state(manager, NAME, "failed")
        assert manager.public_session(NAME)["id"] == started["id"]
        assert manager.find_active(NAME, started["id"]) is None  # ended: no viewer
        await asyncio.sleep(0.3)
        assert manager.public_session(NAME) is None
    finally:
        await _stop_all(manager)


@pytest.mark.asyncio
async def test_find_active_needs_the_session_id(tmp_path):
    env = Env(tmp_path)
    env.add_scraper()
    manager = env.manager()
    try:
        started, _ = await manager.start(NAME)
        assert manager.find_active(NAME, started["id"]) is manager.get(NAME)
        assert manager.find_active(NAME, "wrong") is None
        assert manager.find_active(NAME, "") is None
        assert manager.find_active("other", started["id"]) is None
    finally:
        await _stop_all(manager)


@pytest.mark.asyncio
async def test_shutdown_stops_every_helper(tmp_path):
    env = Env(tmp_path)
    env.add_scraper("one")
    env.add_scraper("two")
    manager = env.manager()
    await manager.start("one")
    await manager.start("two")
    await wait_for_state(manager, "one", "waiting_for_user")
    await wait_for_state(manager, "two", "waiting_for_user")

    await _stop_all(manager)

    for name in ("one", "two"):
        live = manager.get(name)
        assert live.state == "cancelled"
        assert live.process.returncode is not None
        assert not env.registry.login_active(name)
    with pytest.raises(LoginStartError) as e:
        await manager.start("one")
    assert e.value.status_code == 503
