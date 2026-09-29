"""Login routes and the viewer websocket (review rev-nimble-bridge, sections 7.3, 7.4, 7.6).

The routes run in a small FastAPI app with the scrapers routers; the login
manager spawns the fake helper (tests/coordinator/login_fakes). A
TestClient context keeps one event loop alive for the whole test, so the
manager's helper processes and background tasks survive between requests.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import time

import anyio
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from coordinator.api.routes import scrapers as scrapers_routes
from coordinator.services.scraper_login import LoginSessionManager
from sdk.scraper_auth import profile_lock
from tests.coordinator.login_fakes import NAME, OK, Env

WS_URL = "/ws/scrapers/{name}/login?session={id}"


@contextlib.contextmanager
def serve(env: Env, manager: LoginSessionManager, monkeypatch):
    app = FastAPI()
    app.include_router(scrapers_routes.router)
    app.include_router(scrapers_routes.login_ws_router)
    monkeypatch.setattr(scrapers_routes, "_registry", env.registry)
    monkeypatch.setattr(scrapers_routes, "_login_manager", manager)
    with TestClient(app) as client:
        try:
            yield client
        finally:
            client.portal.call(manager.shutdown)


def poll_state(client: TestClient, name: str, *states: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        r = client.get(f"/api/scrapers/{name}/login")
        if r.status_code == 200:
            last = r.json()
            if last["state"] in states:
                return last
        time.sleep(0.02)
    raise AssertionError(f"login session for {name} never reached {states}; last {last}")


def poll(predicate, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("condition not met in time")


def ws_receive(ws, timeout: float = 10.0) -> dict:
    """One raw ASGI message from the server, failing instead of hanging."""
    async def receive():
        with anyio.fail_after(timeout):
            return await ws._send_rx.receive()
    return ws.portal.call(receive)


def ws_json(ws, timeout: float = 10.0) -> dict:
    message = ws_receive(ws, timeout)
    assert message["type"] == "websocket.send", message
    return json.loads(message["text"])


# --- REST ------------------------------------------------------------------------------


def test_post_login_creates_then_reattaches_and_cancel_ends_it(tmp_path, monkeypatch):
    env = Env(tmp_path)
    env.add_scraper()
    manager = env.manager()
    with serve(env, manager, monkeypatch) as client:
        r = client.post(f"/api/scrapers/{NAME}/login")
        assert r.status_code == 201
        created = r.json()
        assert created["state"] == "starting"
        assert created["expires_at"] and created["can_check"] is True

        again = client.post(f"/api/scrapers/{NAME}/login")
        assert again.status_code == 200
        assert again.json()["id"] == created["id"]

        waiting = poll_state(client, NAME, "waiting_for_user")
        assert waiting["id"] == created["id"]
        record = client.get(f"/api/scrapers/{NAME}").json()
        assert record["login_session"]["id"] == created["id"]
        assert record["login_session"]["state"] == "waiting_for_user"
        listed = client.get("/api/scrapers").json()
        assert listed[0]["login_session"]["id"] == created["id"]

        # Run now during the session is refused by the registry check.
        run = client.post(f"/api/scrapers/{NAME}/run").json()
        assert run["success"] is False
        assert run["error"] == "login session in progress"

        r = client.post(f"/api/scrapers/{NAME}/login/cancel")
        assert r.status_code == 202
        ended = poll_state(client, NAME, "cancelled")
        assert ended["id"] == created["id"]
        # A recently ended session stays visible.
        assert client.get(f"/api/scrapers/{NAME}").json()["login_session"]["state"] == "cancelled"
        assert client.post(f"/api/scrapers/{NAME}/login/cancel").status_code == 404


def test_login_routes_404(tmp_path, monkeypatch):
    env = Env(tmp_path)
    env.add_scraper()
    with serve(env, env.manager(), monkeypatch) as client:
        assert client.post("/api/scrapers/missing/login").status_code == 404
        assert client.get("/api/scrapers/missing/login").status_code == 404
        assert client.get(f"/api/scrapers/{NAME}/login").status_code == 404
        assert client.post(f"/api/scrapers/{NAME}/login/check").status_code == 404
        assert client.post(f"/api/scrapers/{NAME}/login/cancel").status_code == 404
        assert client.get(f"/api/scrapers/{NAME}").json()["login_session"] is None


def test_login_without_browser_profile_auth_is_422(tmp_path, monkeypatch):
    env = Env(tmp_path)
    env.add_scraper(auth=False)
    with serve(env, env.manager(), monkeypatch) as client:
        r = client.post(f"/api/scrapers/{NAME}/login")
        assert r.status_code == 422
        assert "no browser-profile login" in r.json()["detail"]


def test_login_while_a_scrape_runs_is_409(tmp_path, monkeypatch):
    env = Env(tmp_path)
    env.add_scraper().last_status = "running"
    with serve(env, env.manager(), monkeypatch) as client:
        r = client.post(f"/api/scrapers/{NAME}/login")
        assert r.status_code == 409
        assert r.json()["detail"] == "a scrape is running; try again when it finishes"
        assert not env.registry.login_active(NAME)


def test_login_while_another_process_holds_the_profile_is_409_with_holder(tmp_path, monkeypatch):
    env = Env(tmp_path)
    env.add_scraper()
    with serve(env, env.manager(), monkeypatch) as client:
        with profile_lock(str(env.profile_dir()), "scrape"):
            r = client.post(f"/api/scrapers/{NAME}/login")
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert detail["holder"]["role"] == "scrape"
        assert detail["holder"]["pid"] == os.getpid()
        assert detail["message"].startswith("the browser profile is in use by")
        assert not env.registry.login_active(NAME)
        assert client.get(f"/api/scrapers/{NAME}/login").status_code == 404


def test_a_third_concurrent_session_is_429(tmp_path, monkeypatch):
    env = Env(tmp_path)
    for name in ("one", "two", "three"):
        env.add_scraper(name)
    with serve(env, env.manager(), monkeypatch) as client:
        assert client.post("/api/scrapers/one/login").status_code == 201
        assert client.post("/api/scrapers/two/login").status_code == 201
        r = client.post("/api/scrapers/three/login")
        assert r.status_code == 429
        assert "at most 2" in r.json()["detail"]
        assert not env.registry.login_active("three")
        # Once one ends there is room again.
        client.post("/api/scrapers/one/login/cancel")
        poll_state(client, "one", "cancelled")
        assert client.post("/api/scrapers/three/login").status_code == 201


def test_headed_login_without_a_display_is_503(tmp_path, monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    env = Env(tmp_path)
    env.add_scraper(headless=False)
    with serve(env, env.manager(), monkeypatch) as client:
        r = client.post(f"/api/scrapers/{NAME}/login")
        assert r.status_code == 503
        assert "no X display" in r.json()["detail"]
        assert "xvfb-run" in r.json()["detail"]
        assert not env.registry.login_active(NAME)


def test_check_forwards_to_the_helper_and_a_verified_check_confirms(tmp_path, monkeypatch):
    env = Env(tmp_path, [OK])
    env.add_scraper()
    env.set_needs_login()
    with serve(env, env.manager("interactive", "--check-verifies"), monkeypatch) as client:
        client.post(f"/api/scrapers/{NAME}/login")
        poll_state(client, NAME, "waiting_for_user")
        r = client.post(f"/api/scrapers/{NAME}/login/check")
        assert r.status_code == 202
        ended = poll_state(client, NAME, "succeeded", "confirm_failed", "failed")
        assert ended["state"] == "succeeded"
        assert '{"cmd":"check"}' in env.recorded()
        assert env.runs == [{"name": NAME, "auth_state": None}]
        record = client.get(f"/api/scrapers/{NAME}").json()
        assert record["auth_state"] == "ok"
        assert record["schedule_paused"] is False


# --- websocket ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", [
    "/ws/scrapers/{name}/login?session=not-a-session",
    "/ws/scrapers/{name}/login",
    "/ws/scrapers/missing/login?session={id}",
])
def test_websocket_unknown_session_closes_4404(tmp_path, monkeypatch, url):
    env = Env(tmp_path)
    env.add_scraper()
    with serve(env, env.manager(), monkeypatch) as client:
        session = client.post(f"/api/scrapers/{NAME}/login").json()
        with client.websocket_connect(url.format(name=NAME, id=session["id"])) as ws:
            message = ws_receive(ws)
        assert message["type"] == "websocket.close"
        assert message["code"] == 4404


def test_websocket_relays_frames_and_input_and_ends_with_1000(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    secret = "hunter2-correct-horse"
    env = Env(tmp_path)
    env.add_scraper()
    with serve(env, env.manager(), monkeypatch) as client:
        session = client.post(f"/api/scrapers/{NAME}/login").json()
        poll_state(client, NAME, "waiting_for_user")
        poll(lambda: client.portal.call(_has_frame, NAME))
        with client.websocket_connect(WS_URL.format(name=NAME, id=session["id"])) as ws:
            hello = ws_json(ws)
            assert hello["type"] == "session"
            assert hello["session"]["id"] == session["id"]
            assert hello["session"]["state"] == "waiting_for_user"
            cached = [ws_json(ws), ws_json(ws)]
            assert [m["type"] for m in cached] == ["pages", "frame"]
            assert cached[0]["pages"][0]["url"] == "https://example.test/login"
            assert cached[1]["metadata"]["deviceWidth"] == 1280

            ws.send_json({"cmd": "mouse", "action": "down", "x": 10, "y": 20})
            ws.send_json({"cmd": "text", "text": secret})
            ws.send_json({"cmd": "text", "text": secret * 20})           # oversize: dropped
            ws.send_json({"cmd": "navigate", "target": "https://evil.test"})  # not a target
            ws.send_text("{" + secret)                                    # not JSON
            ws.send_json({"cmd": "cancel"})

            seen = []
            while True:
                message = ws_json(ws)
                seen.append(message)
                if message["type"] == "ended":
                    break
            assert seen[-1]["session"]["state"] == "cancelled"
            closing = ws_receive(ws)
            assert closing["type"] == "websocket.close"
            assert closing["code"] == 1000

        assert env.recorded() == [
            '{"cmd":"mouse","action":"down","x":10.0,"y":20.0,"button":"left","click_count":1}',
            json.dumps({"cmd": "text", "text": secret}, separators=(",", ":")),
            '{"cmd":"cancel"}',
        ]
        assert secret not in caplog.text
        assert all(secret not in str(r.args) for r in caplog.records)

        # The ended session can't be viewed any more.
        with client.websocket_connect(WS_URL.format(name=NAME, id=session["id"])) as ws:
            message = ws_receive(ws)
        assert message["type"] == "websocket.close"
        assert message["code"] == 4404


def test_websocket_viewer_leaving_does_not_end_the_session(tmp_path, monkeypatch):
    env = Env(tmp_path)
    env.add_scraper()
    with serve(env, env.manager(), monkeypatch) as client:
        session = client.post(f"/api/scrapers/{NAME}/login").json()
        poll_state(client, NAME, "waiting_for_user")
        with client.websocket_connect(WS_URL.format(name=NAME, id=session["id"])) as ws:
            assert ws_json(ws)["type"] == "session"
        with client.websocket_connect(WS_URL.format(name=NAME, id=session["id"])) as ws:
            assert ws_json(ws)["session"]["state"] == "waiting_for_user"
        assert client.get(f"/api/scrapers/{NAME}/login").json()["state"] == "waiting_for_user"
        poll(lambda: not client.portal.call(_viewer_count, NAME))


async def _has_frame(name: str) -> bool:
    session = scrapers_routes._login_manager.get(name)
    return session is not None and session.latest_frame is not None


async def _viewer_count(name: str) -> int:
    return len(scrapers_routes._login_manager.get(name).viewers)


# --- lifespan ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifespan_wires_the_login_manager_and_shuts_it_down(monkeypatch):
    from coordinator.main import create_app

    # Restore the module globals the lifespan sets.
    monkeypatch.setattr(scrapers_routes, "_registry", scrapers_routes._registry)
    monkeypatch.setattr(scrapers_routes, "_login_manager", scrapers_routes._login_manager)

    app = create_app(database_url="sqlite+aiosqlite:///:memory:")
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/ws/scrapers/{name}/login" in paths
    assert "/api/scrapers/{name}/login" in paths
    async with app.router.lifespan_context(app):
        manager = app.state.scraper_login_manager
        assert isinstance(manager, LoginSessionManager)
        assert scrapers_routes._login_manager is manager
        assert manager._shutting_down is False
    assert manager._shutting_down is True
