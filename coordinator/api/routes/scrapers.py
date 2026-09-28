from __future__ import annotations

import logging
from typing import Optional

import anyio
from fastapi import APIRouter, HTTPException, Response, WebSocket
from pydantic import BaseModel
from starlette.websockets import WebSocketState

from coordinator.services.package_manager import PackageError
from coordinator.services.scraper_engine import runs_headed
from coordinator.services.scraper_login import LoginSessionManager, LoginStartError
from coordinator.services.scraper_registry import ScraperRecord, ScraperRegistry

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/scrapers", tags=["scrapers"])
# The login viewer socket lives outside /api (review rev-nimble-bridge 7.4).
login_ws_router = APIRouter(tags=["scrapers"])

# Close code for an unknown or ended login session.
WS_CLOSE_UNKNOWN_SESSION = 4404


class ScraperInstall(BaseModel):
    repo_url: str
    name: Optional[str] = None

_registry: Optional[ScraperRegistry] = None
_login_manager: Optional[LoginSessionManager] = None


def set_registry(registry: ScraperRegistry) -> None:
    global _registry
    _registry = registry


def set_login_manager(manager: Optional[LoginSessionManager]) -> None:
    global _login_manager
    _login_manager = manager


def _require_registry() -> ScraperRegistry:
    if _registry is None:
        raise HTTPException(status_code=503, detail="scraper registry not initialized")
    return _registry


def _require_login_manager() -> LoginSessionManager:
    if _login_manager is None:
        raise HTTPException(status_code=503, detail="scraper login is not initialized")
    return _login_manager


def _next_run_for(reg: ScraperRegistry, name: str) -> Optional[str]:
    for job in reg._scheduler.list_jobs():  # noqa: SLF001
        if job["id"] == f"scraper:{name}":
            return job.get("next_run")
    return None


def _auth_to_dict(record: ScraperRecord) -> Optional[dict]:
    """The scraper's `auth:` block as the dashboard needs it; None without one."""
    if record.auth is None:
        return None
    return {
        "kind": record.auth.kind,
        "login_supported": record.auth.kind == "browser_profile",
        "headed": runs_headed(record.manifest, record.config),
    }


async def _record_to_dict(record, reg: ScraperRegistry) -> dict:
    state = await reg.get_persistent_state(record.name)
    return {
        "name": record.name,
        "schedule": record.schedule,
        "jitter_seconds": record.jitter_seconds,
        "next_run_at": _next_run_for(reg, record.name),
        "version": record.manifest.get("version"),
        "description": record.manifest.get("description"),
        "config_overrides": sorted(record.config.keys()),
        "last_status": state["last_status"],
        "last_run_at": state["last_run_at"],
        "last_error": state["last_error"],
        "attempts_today": state["attempts_today"],
        "data_url": f"/api/data/custom/{record.name}",
        "auth": _auth_to_dict(record),
        "auth_state": state["auth_state"],
        "auth_reason": state["auth_reason"],
        "auth_message": state["auth_message"],
        "auth_changed_at": state["auth_changed_at"],
        "schedule_paused": state["auth_state"] == "needs_login",
        # {id, state, message, started_at, expires_at, can_check} while a login
        # session is active or ended less than 10 minutes ago, else null.
        "login_session": (
            _login_manager.public_session(record.name) if _login_manager is not None else None
        ),
    }


@router.get("")
async def list_scrapers():
    reg = _require_registry()
    return [await _record_to_dict(r, reg) for r in reg.list_records()]


@router.get("/{name}")
async def get_scraper(name: str):
    reg = _require_registry()
    record = reg.get(name)
    if record is None:
        raise HTTPException(status_code=404, detail=f"scraper {name!r} not found")
    return await _record_to_dict(record, reg)


@router.post("/{name}/run")
async def run_scraper_now(name: str):
    reg = _require_registry()
    record = reg.get(name)
    if record is None:
        raise HTTPException(status_code=404, detail=f"scraper {name!r} not found")
    result = await reg.run(name, trigger="manual")
    return {
        "success": result.success,
        "error": result.error,
        "error_kind": result.error_kind,
        "record": await _record_to_dict(record, reg),
    }


@router.post("", status_code=201)
async def install_scraper(body: ScraperInstall):
    """Clone a scraper repo, install its deps, validate manifest, register on the scheduler."""
    reg = _require_registry()
    # Run synchronously in a thread — clone + pip install can take ~30s.
    import asyncio
    try:
        record = await asyncio.to_thread(reg.install_scraper, body.repo_url, body.name)
    except PackageError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("scraper install failed for %s", body.repo_url)
        raise HTTPException(status_code=500, detail=f"Install failed: {e}")
    return await _record_to_dict(record, reg)


@router.delete("/{name}", status_code=204)
async def delete_scraper(name: str):
    reg = _require_registry()
    if reg.get(name) is None:
        raise HTTPException(status_code=404, detail=f"scraper {name!r} not found")
    import asyncio
    await asyncio.to_thread(reg.uninstall_scraper, name)


# --- Login sessions (review rev-nimble-bridge 7.3-7.6) ---------------------------

@router.post("/{name}/login")
async def start_scraper_login(name: str, response: Response):
    """Open a login session: 201 with a new one, 200 with the one already active.

    404 unknown scraper, 422 no browser-profile auth block, 409 a scrape is
    running or another process holds the profile (detail.holder), 429 too
    many sessions, 503 a headed browser with no X display.
    """
    manager = _require_login_manager()
    try:
        session, created = await manager.start(name)
    except LoginStartError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None
    response.status_code = 201 if created else 200
    return session


@router.get("/{name}/login")
async def get_scraper_login(name: str):
    """The scraper's active login session, or one that ended in the last 10 minutes."""
    reg = _require_registry()
    manager = _require_login_manager()
    if reg.get(name) is None:
        raise HTTPException(status_code=404, detail=f"scraper {name!r} not found")
    session = manager.public_session(name)
    if session is None:
        raise HTTPException(status_code=404, detail=f"no login session for {name}")
    return session


@router.post("/{name}/login/check", status_code=202)
async def check_scraper_login(name: str):
    """Ask the helper for an active check ("Check now")."""
    manager = _require_login_manager()
    try:
        return await manager.check(name)
    except LoginStartError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@router.post("/{name}/login/cancel", status_code=202)
async def cancel_scraper_login(name: str):
    """Start the stop sequence; the session ends as cancelled once the browser is gone."""
    manager = _require_login_manager()
    try:
        return await manager.cancel(name)
    except LoginStartError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from None


@login_ws_router.websocket("/ws/scrapers/{name}/login")
async def scraper_login_socket(websocket: WebSocket, name: str) -> None:
    """A login session viewer: session, frame, pages and ended out; input in.

    `?session=<id>` must name the scraper's active session, otherwise the
    socket closes with 4404. Input is validated and written to the helper,
    and never logged. Closes with 1000 after `ended`; a viewer leaving does
    not end the session.
    """
    await websocket.accept()
    manager = _login_manager
    session_id = websocket.query_params.get("session", "")
    session = manager.find_active(name, session_id) if manager is not None else None
    if session is None:
        await websocket.close(code=WS_CLOSE_UNKNOWN_SESSION)
        return
    viewer = manager.attach_viewer(session)

    async def send_loop() -> None:
        while True:
            text = await viewer.next()
            if text is None:
                return
            try:
                await websocket.send_text(text)
            except Exception:  # noqa: BLE001 — the viewer went away
                return

    async def receive_loop() -> None:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            data = message.get("text")
            if data is None:
                data = message.get("bytes")
            if data is not None:
                await manager.handle_viewer_input(session, data)

    async def until_done(loop, scope: anyio.CancelScope) -> None:
        # Whichever side finishes first (ended sent, or the viewer left) ends both.
        try:
            await loop()
        finally:
            scope.cancel()

    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(until_done, send_loop, tg.cancel_scope)
            tg.start_soon(until_done, receive_loop, tg.cancel_scope)
    finally:
        manager.detach_viewer(session, viewer)
    if viewer.finished and websocket.application_state == WebSocketState.CONNECTED:
        try:
            await websocket.close(code=1000)
        except Exception:  # noqa: BLE001 — the viewer went away first
            pass
