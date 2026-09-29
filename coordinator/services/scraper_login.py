"""Dashboard login sessions for scrapers (review rev-nimble-bridge, sections 7.3-7.6).

LoginSessionManager spawns the login helper (sdk/scraper_login.py) in the
scraper package's venv, supervises it and relays it to dashboard viewers:

- The helper speaks JSON lines: events on its stdout (ready, frame, pages,
  status, verified, heartbeat, error, closed), commands on its stdin.
- Viewers connect over WS /ws/scrapers/{name}/login?session=<id>. Each has a
  one-slot latest-frame mailbox, so a slow viewer skips frames instead of
  queueing them. Their input is validated (parse_viewer_command) and written
  to the helper's stdin.
- A watchdog kills a helper that stops heartbeating and cancels one that
  outlives its session. Stopping escalates cancel -> SIGTERM -> SIGKILL of the
  helper's process group, and a /proc scan then reaps any Chromium still
  running on the profile (Playwright starts the browser in its own group).
- Once the helper has exited, a verified sign-in (or the person closing the
  browser) is followed by one confirmation scrape.

Nothing here logs viewer input, frames or the helper's stderr: they can carry
what the person types and what the page shows.
"""
from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import secrets
import signal
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Awaitable, Callable, Literal, Optional, Union

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from coordinator.services.scraper_engine import X11_SOCKET_DIR, child_env, runs_headed
from coordinator.services.scraper_registry import (
    AUTH_ERROR_KINDS,
    ScrapeRunning,
    ScraperRegistry,
)
from sdk.scraper_auth import (
    AuthConfigError,
    describe_holder,
    read_profile_holder,
    resolve_profile_dir,
)
from sdk.scraper_login import (
    MAX_CLICK_COUNT,
    MAX_CODE_CHARS,
    MAX_COMMAND_BYTES,
    MAX_COORDINATE,
    MAX_KEY_CHARS,
    MAX_PAGE_ID_CHARS,
    MAX_TEXT_CHARS,
    MAX_WHEEL_DELTA,
    helper_command as build_helper_command,
)

logger = logging.getLogger(__name__)

# Session states. The helper drives waiting_for_user <-> checking; the rest
# are the manager's. browser_closed goes on to confirming, like verified.
ACTIVE_STATES = (
    "starting", "waiting_for_user", "checking", "verified", "browser_closed", "confirming",
)
TERMINAL_STATES = ("succeeded", "confirm_failed", "cancelled", "timed_out", "failed")
HELPER_STATES = ("waiting_for_user", "checking")

MAX_SESSIONS = 2                    # across all scrapers
STREAM_LIMIT = 16 * 1024 * 1024     # one frame line is far over asyncio's 64 KiB default
STDERR_TAIL_LINES = 50
STDERR_LINE_MAX_CHARS = 500
HEARTBEAT_TIMEOUT_S = 30.0
CANCEL_WAIT_S = 10.0                # stop step 1: cancel, then wait
TERM_WAIT_S = 5.0                   # stop steps 2-3: after SIGTERM / SIGKILL
REAP_GRACE_S = 5.0                  # stop step 4: stragglers get this long to exit
SHUTDOWN_CANCEL_WAIT_S = 2.0
SHUTDOWN_TERM_WAIT_S = 1.0
SHUTDOWN_REAP_GRACE_S = 1.0
SHUTDOWN_LIFECYCLE_WAIT_S = 10.0
WATCHDOG_INTERVAL_S = 1.0
READER_DRAIN_S = 5.0                # after exit, for the last stdout lines (closed)
RECENT_S = 600.0                    # an ended session stays visible this long
MESSAGE_MAX_CHARS = 1000
ERROR_TAIL_LINES = 5

HelperCommand = Callable[[str, str, str, dict], list[str]]
Broadcast = Callable[[dict], Awaitable[None]]


class LoginStartError(Exception):
    """A login request refused; `status_code` and `detail` become the HTTP response."""

    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(detail if isinstance(detail, str) else detail.get("message", ""))
        self.status_code = status_code
        self.detail = detail


# --- Viewer input (review 7.4): the helper commands a viewer may send -------

Coordinate = Annotated[
    float, Field(strict=True, ge=-MAX_COORDINATE, le=MAX_COORDINATE, allow_inf_nan=False),
]
WheelDelta = Annotated[
    float, Field(strict=True, ge=-MAX_WHEEL_DELTA, le=MAX_WHEEL_DELTA, allow_inf_nan=False),
]


class MouseCommand(BaseModel):
    cmd: Literal["mouse"]
    action: Literal["move", "down", "up"]
    x: Coordinate
    y: Coordinate
    button: Literal["left", "middle", "right"] = "left"
    click_count: Annotated[int, Field(strict=True, ge=1, le=MAX_CLICK_COUNT)] = 1


class WheelCommand(BaseModel):
    cmd: Literal["wheel"]
    x: Coordinate
    y: Coordinate
    dx: WheelDelta
    dy: WheelDelta


class KeyCommand(BaseModel):
    cmd: Literal["key"]
    action: Literal["down", "up"]
    key: Annotated[str, Field(strict=True, min_length=1, max_length=MAX_KEY_CHARS)]
    code: Optional[Annotated[str, Field(strict=True, max_length=MAX_CODE_CHARS)]] = None


class TextCommand(BaseModel):
    cmd: Literal["text"]
    text: Annotated[str, Field(strict=True, min_length=1, max_length=MAX_TEXT_CHARS)]


class SwitchPageCommand(BaseModel):
    cmd: Literal["switch_page"]
    id: Annotated[str, Field(strict=True, min_length=1, max_length=MAX_PAGE_ID_CHARS)]


class HistoryCommand(BaseModel):
    cmd: Literal["history"]
    action: Literal["back", "forward", "reload"]


class NavigateCommand(BaseModel):
    cmd: Literal["navigate"]
    target: Literal["login", "verify"]


class CheckCommand(BaseModel):
    cmd: Literal["check"]


class CancelCommand(BaseModel):
    cmd: Literal["cancel"]


ViewerCommand = Annotated[
    Union[
        MouseCommand, WheelCommand, KeyCommand, TextCommand, SwitchPageCommand,
        HistoryCommand, NavigateCommand, CheckCommand, CancelCommand,
    ],
    Field(discriminator="cmd"),
]
_VIEWER_COMMAND = TypeAdapter(ViewerCommand)


def parse_viewer_command(text: Union[str, bytes]) -> Optional[dict]:
    """The validated command a viewer sent, or None when it must be dropped.

    Drops oversize lines, non-JSON, unknown commands, out-of-range numbers and
    oversize text; unknown extra fields are stripped. Never logs the input:
    a ValidationError's text quotes it, and it can be what someone typed.
    """
    if len(text) > MAX_COMMAND_BYTES:
        return None
    try:
        obj = json.loads(text)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(obj, dict):
        return None
    try:
        command = _VIEWER_COMMAND.validate_python(obj)
    except ValidationError:
        return None
    return command.model_dump(exclude_none=True)


# --- Viewers -----------------------------------------------------------------

class LoginViewer:
    """One websocket viewer's outbox: ordered session messages plus latest-only slots.

    session messages queue in order. pages and frame keep only the newest
    value, so a viewer that can't keep up skips frames instead of queueing
    them (review 7.4 back-pressure). ended goes out after the queued session
    messages and is the last message; `finished` turns true once it is taken.
    Messages are JSON text.
    """

    def __init__(self) -> None:
        self._control: collections.deque[str] = collections.deque()
        self._pages: Optional[str] = None
        self._frame: Optional[str] = None
        self._ended: Optional[str] = None
        self._wake = asyncio.Event()
        self.finished = False

    def post(self, text: str) -> None:
        if self._ended is None:
            self._control.append(text)
            self._wake.set()

    def post_ended(self, text: str) -> None:
        if self._ended is None:
            self._ended = text
            self._wake.set()

    def post_pages(self, text: str) -> None:
        if self._ended is None:
            self._pages = text
            self._wake.set()

    def post_frame(self, text: str) -> None:
        if self._ended is None:
            self._frame = text
            self._wake.set()

    def pending(self) -> Optional[str]:
        """The next message to send, without waiting; None when there is none."""
        if self.finished:
            return None
        if self._control:
            return self._control.popleft()
        if self._ended is not None:
            self.finished = True
            return self._ended
        if self._pages is not None:
            text, self._pages = self._pages, None
            return text
        if self._frame is not None:
            text, self._frame = self._frame, None
            return text
        return None

    async def next(self) -> Optional[str]:
        """The next message, waiting for one; None once ended has been taken."""
        while not self.finished:
            text = self.pending()
            if text is not None:
                return text
            self._wake.clear()
            await self._wake.wait()
        return None


# --- Sessions ----------------------------------------------------------------

def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _clip(text: Any, limit: int = MESSAGE_MAX_CHARS) -> Optional[str]:
    if text is None:
        return None
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass
class LoginSession:
    id: str
    name: str
    profile_dir: str
    started_at: datetime
    expires_at: datetime
    can_check: bool
    state: str = "starting"
    message: Optional[str] = None
    ended_at: Optional[datetime] = None
    process: Optional[asyncio.subprocess.Process] = None
    headed: bool = False
    latest_frame: Optional[str] = None
    latest_pages: Optional[str] = None
    viewers: set = field(default_factory=set)
    stderr_tail: collections.deque = field(
        default_factory=lambda: collections.deque(maxlen=STDERR_TAIL_LINES),
    )
    last_heartbeat: float = 0.0
    verified: bool = False
    closed_reason: Optional[str] = None
    helper_error: Optional[dict] = None
    # Set when the manager ends the session (cancel, timeout, dead helper):
    # the state the session ends in once the helper has exited.
    stop_state: Optional[str] = None
    stop_message: Optional[str] = None
    stop_task: Optional[asyncio.Task] = None
    reader_task: Optional[asyncio.Task] = None
    tasks: set = field(default_factory=set)
    stdin_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def ended(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def helper_running(self) -> bool:
        return self.process is not None and self.process.returncode is None

    def public(self) -> dict:
        """The session as the API and viewers see it (never the helper's internals)."""
        return {
            "id": self.id,
            "state": self.state,
            "message": self.message,
            "started_at": _iso(self.started_at),
            "expires_at": _iso(self.expires_at),
            "can_check": self.can_check,
        }


def find_profile_processes(
    profile_dir: str, *, proc_root: str = "/proc", exclude: Optional[set] = None,
) -> set[int]:
    """PIDs whose argv names `profile_dir` as Chromium's --user-data-dir.

    Zombies have an empty cmdline and so never match. Unreadable entries
    (other users, processes that just exited) are skipped.
    """
    target = os.path.normpath(profile_dir)
    exclude = exclude if exclude is not None else {os.getpid()}
    found: set[int] = set()
    try:
        entries = os.listdir(proc_root)
    except OSError:
        return found
    for entry in entries:
        if not entry.isdigit() or int(entry) in exclude:
            continue
        try:
            with open(os.path.join(proc_root, entry, "cmdline"), "rb") as f:
                raw = f.read()
        except OSError:
            continue
        args = [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]
        for i, arg in enumerate(args):
            if arg.startswith("--user-data-dir="):
                value = arg[len("--user-data-dir="):]
            elif arg == "--user-data-dir" and i + 1 < len(args):
                value = args[i + 1]
            else:
                continue
            if value and os.path.normpath(value) == target:
                found.add(int(entry))
                break
    return found


def _count_csv_rows(path: Optional[str]) -> Optional[int]:
    if not path:
        return None
    try:
        with open(path) as f:
            return max(0, sum(1 for _ in f) - 1)
    except OSError:
        return None


class LoginSessionManager:
    """Starts, supervises and relays scraper login sessions (review 7.3)."""

    def __init__(
        self,
        registry: ScraperRegistry,
        *,
        quilt_root: Optional[str] = None,
        broadcast: Optional[Broadcast] = None,
        x11_socket_dir: str = X11_SOCKET_DIR,
        helper_command: Optional[HelperCommand] = None,
        max_sessions: int = MAX_SESSIONS,
        stream_limit: int = STREAM_LIMIT,
        heartbeat_timeout_s: float = HEARTBEAT_TIMEOUT_S,
        cancel_wait_s: float = CANCEL_WAIT_S,
        term_wait_s: float = TERM_WAIT_S,
        reap_grace_s: float = REAP_GRACE_S,
        watchdog_interval_s: float = WATCHDOG_INTERVAL_S,
        recent_s: float = RECENT_S,
        proc_root: str = "/proc",
    ) -> None:
        if quilt_root is None:
            quilt_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self._registry = registry
        self._quilt_root = quilt_root
        self._broadcast = broadcast
        self._x11_socket_dir = x11_socket_dir
        self._helper_command = helper_command or (
            lambda python, pkg_dir, root, config: build_helper_command(python, pkg_dir, root, config)
        )
        self._max_sessions = max_sessions
        self._stream_limit = stream_limit
        self._heartbeat_timeout_s = heartbeat_timeout_s
        self._cancel_wait_s = cancel_wait_s
        self._term_wait_s = term_wait_s
        self._reap_grace_s = reap_grace_s
        self._watchdog_interval_s = watchdog_interval_s
        self._recent_s = recent_s
        self._proc_root = proc_root
        # The latest session per scraper, active or recently ended.
        self._sessions: dict[str, LoginSession] = {}
        self._lifecycles: set[asyncio.Task] = set()
        self._shutting_down = False

    # --- queries -------------------------------------------------------------

    def _prune(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self._recent_s)
        for name, session in list(self._sessions.items()):
            if session.ended and session.ended_at is not None and session.ended_at <= cutoff:
                del self._sessions[name]

    def get(self, name: str) -> Optional[LoginSession]:
        """The scraper's active session, or one that ended within the last 10 minutes."""
        self._prune()
        return self._sessions.get(name)

    def public_session(self, name: str) -> Optional[dict]:
        session = self.get(name)
        return session.public() if session is not None else None

    def find_active(self, name: str, session_id: str) -> Optional[LoginSession]:
        """The scraper's session with this id, unless it is unknown or has ended."""
        session = self._sessions.get(name)
        if session is None or session.ended or not session_id:
            return None
        if not secrets.compare_digest(session.id.encode(), session_id.encode()):
            return None
        return session

    def _active_count(self) -> int:
        return sum(1 for s in self._sessions.values() if not s.ended)

    # --- start (review 7.3) ----------------------------------------------------

    async def start(self, name: str) -> tuple[dict, bool]:
        """Start a login session for `name`; returns (session, created).

        created is False when a session for this scraper is already active:
        the dashboard reattaches to it instead of opening a second browser.
        Raises LoginStartError with 404 / 409 / 422 / 429 / 503 (500 when the
        helper can't be spawned at all).
        """
        registry = self._registry
        record = registry.get(name)
        if record is None:
            raise LoginStartError(404, f"scraper {name!r} not found")
        if record.auth is None or record.auth.kind != "browser_profile":
            raise LoginStartError(
                422, f"scraper {name} has no browser-profile login (no auth: block in its quilt.yaml)",
            )
        current = self.get(name)
        if current is not None and not current.ended:
            return current.public(), False
        if self._shutting_down:
            raise LoginStartError(503, "the coordinator is shutting down")

        pkg_dir = os.path.join(registry.packages_dir, name)
        try:
            profile_dir = resolve_profile_dir(record.manifest, record.config, base_dir=pkg_dir)
        except AuthConfigError as e:
            raise LoginStartError(422, f"scraper {name}: {e}") from None

        # Everything from here to the session being registered is synchronous,
        # so a concurrent start or run cannot slip in between.
        try:
            registry.begin_login(name)
        except ScrapeRunning as e:
            raise LoginStartError(409, str(e)) from None
        except KeyError:
            raise LoginStartError(404, f"scraper {name!r} not found") from None
        try:
            holder = read_profile_holder(profile_dir)
            if holder is not None:
                raise LoginStartError(409, {
                    "message": f"the browser profile is {describe_holder(holder)}",
                    "holder": holder,
                })
            if self._active_count() >= self._max_sessions:
                raise LoginStartError(
                    429, f"at most {self._max_sessions} login sessions can be open at once",
                )
            env, env_error = child_env(
                name, record.manifest, record.config, x11_socket_dir=self._x11_socket_dir,
            )
            if env_error is not None:
                raise LoginStartError(503, env_error)
        except BaseException:
            registry.end_login(name)
            raise

        now = datetime.now(timezone.utc)
        session = LoginSession(
            id=secrets.token_urlsafe(16),
            name=name,
            profile_dir=profile_dir,
            started_at=now,
            expires_at=now + timedelta(seconds=record.auth.session_timeout_s),
            can_check=bool(record.auth.verify.url),
            headed=runs_headed(record.manifest, record.config),
            message="Starting the browser…",
        )
        self._sessions[name] = session

        venv_python = os.path.join(pkg_dir, ".venv", "bin", "python")
        python = venv_python if os.path.exists(venv_python) else sys.executable
        argv = self._helper_command(python, pkg_dir, self._quilt_root, dict(record.config))
        try:
            session.process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=pkg_dir,
                env=env,
                start_new_session=True,
                limit=self._stream_limit,
            )
        except (OSError, ValueError) as e:
            message = f"could not start the login helper: {type(e).__name__}: {e}"
            self._end(session, "failed", message)
            logger.warning("login session for %s: %s", name, message)
            raise LoginStartError(500, message) from None

        loop = asyncio.get_running_loop()
        session.last_heartbeat = loop.time()
        session.reader_task = self._spawn(session, self._read_events(session))
        self._spawn(session, self._drain_stderr(session))
        self._spawn(session, self._watchdog(session))
        lifecycle = asyncio.create_task(self._lifecycle(session))
        self._lifecycles.add(lifecycle)
        lifecycle.add_done_callback(self._lifecycles.discard)
        logger.info(
            "login session for %s started: helper pid %s, profile %s, expires %s",
            name, session.process.pid, profile_dir, _iso(session.expires_at),
        )
        await self._broadcast_state(session)
        if session.stop_state is not None:  # cancelled while the helper was spawning
            self._request_stop(session, session.stop_state, session.stop_message)
        return session.public(), True

    @staticmethod
    def _spawn(session: LoginSession, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        session.tasks.add(task)
        task.add_done_callback(session.tasks.discard)
        return task

    # --- control ---------------------------------------------------------------

    def _require_active(self, name: str) -> LoginSession:
        if self._registry.get(name) is None:
            raise LoginStartError(404, f"scraper {name!r} not found")
        session = self.get(name)
        if session is None or session.ended:
            raise LoginStartError(404, f"no active login session for {name}")
        return session

    async def check(self, name: str) -> dict:
        """Forward an active check ("Check now") to the helper."""
        session = self._require_active(name)
        if session.stop_state is not None or not session.helper_running:
            raise LoginStartError(409, f"the login session for {name} is {session.state}")
        await self._send_command(session, {"cmd": "check"})
        return session.public()

    async def cancel(self, name: str) -> dict:
        """Start the stop sequence; the session ends as cancelled once the helper is gone."""
        session = self._require_active(name)
        if session.state == "confirming":
            raise LoginStartError(409, "the confirmation scrape is running; it finishes on its own")
        self._request_stop(session, "cancelled", "The login session was cancelled.")
        return session.public()

    async def handle_viewer_input(self, session: LoginSession, text: Union[str, bytes]) -> None:
        """Validate one viewer message and pass it to the helper; invalid input is dropped."""
        command = parse_viewer_command(text)
        if command is None:
            logger.debug("login session for %s: dropped an invalid viewer command", session.name)
            return
        if session.ended or session.stop_state is not None:
            return
        if command["cmd"] == "cancel":
            if session.state != "confirming":
                self._request_stop(session, "cancelled", "The login session was cancelled.")
            return
        await self._send_command(session, command)

    async def _send_command(self, session: LoginSession, command: dict) -> bool:
        """Write one JSON line to the helper's stdin. Never logs the command."""
        proc = session.process
        if proc is None or proc.returncode is not None or proc.stdin is None:
            return False
        line = (json.dumps(command, separators=(",", ":")) + "\n").encode()
        async with session.stdin_lock:
            if proc.stdin.is_closing():
                return False
            try:
                proc.stdin.write(line)
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError, RuntimeError):
                return False
        return True

    # --- viewers ---------------------------------------------------------------

    def attach_viewer(self, session: LoginSession) -> LoginViewer:
        """Register a viewer and queue the session, the latest frame and the pages for it."""
        viewer = LoginViewer()
        viewer.post(json.dumps({"type": "session", "session": session.public()}))
        if session.latest_frame is not None:
            viewer.post_frame(session.latest_frame)
        if session.latest_pages is not None:
            viewer.post_pages(session.latest_pages)
        if session.ended:
            viewer.post_ended(json.dumps({"type": "ended", "session": session.public()}))
        session.viewers.add(viewer)
        return viewer

    @staticmethod
    def detach_viewer(session: LoginSession, viewer: LoginViewer) -> None:
        session.viewers.discard(viewer)

    def _push_session(self, session: LoginSession) -> None:
        text = json.dumps({"type": "session", "session": session.public()})
        for viewer in list(session.viewers):
            viewer.post(text)

    # --- state -------------------------------------------------------------------

    def _end(self, session: LoginSession, state: str, message: Optional[str]) -> bool:
        """Move to a terminal state without broadcasting; False when already ended.

        Clears the registry's login flag in the same step, so the flag covers
        the confirmation scrape and a new session can never have its own flag
        cleared by this one.
        """
        if session.ended:
            return False
        session.state = state
        session.message = _clip(message)
        session.ended_at = datetime.now(timezone.utc)
        self._registry.end_login(session.name)
        ended = json.dumps({"type": "ended", "session": session.public()})
        for viewer in list(session.viewers):
            viewer.post_ended(ended)
        logger.info("login session for %s ended: %s", session.name, state)
        return True

    async def _set_state(self, session: LoginSession, state: str, message: Optional[str] = None) -> None:
        if session.ended:
            return
        if state in TERMINAL_STATES:
            if self._end(session, state, message):
                await self._broadcast_state(session)
            return
        changed = state != session.state
        session.state = state
        if message is not None:
            session.message = _clip(message)
        self._push_session(session)
        if changed:
            logger.info("login session for %s: %s", session.name, state)
            await self._broadcast_state(session)

    async def _broadcast_state(self, session: LoginSession) -> None:
        if self._broadcast is None:
            return
        try:
            await self._broadcast({
                "type": "scraper_login_state", "name": session.name, "state": session.state,
            })
        except Exception as e:  # noqa: BLE001 — the API is the record; this is a courtesy
            logger.warning("failed to broadcast login state for %s: %s", session.name, e)

    # --- helper output -------------------------------------------------------------

    async def _read_events(self, session: LoginSession) -> None:
        stream = session.process.stdout
        while True:
            try:
                line = await stream.readline()
            except ValueError:
                # Over the stream limit: readline dropped what it had buffered.
                logger.warning(
                    "login helper for %s sent a line over %d bytes; dropped it",
                    session.name, self._stream_limit,
                )
                continue
            if not line:
                return
            try:
                event = json.loads(line)
            except ValueError:
                logger.debug("login helper for %s sent a line that is not JSON", session.name)
                continue
            if isinstance(event, dict):
                try:
                    await self._on_event(session, event)
                except Exception:  # noqa: BLE001 — one bad event must not stop the relay
                    logger.exception("login session for %s: failed to handle a helper event", session.name)

    async def _on_event(self, session: LoginSession, event: dict) -> None:
        kind = event.get("type")
        if kind == "heartbeat":
            session.last_heartbeat = asyncio.get_running_loop().time()
        elif kind == "frame":
            data = event.get("data")
            if not isinstance(data, str):
                return
            metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
            text = json.dumps({"type": "frame", "data": data, "metadata": metadata})
            session.latest_frame = text
            for viewer in list(session.viewers):
                viewer.post_frame(text)
        elif kind == "pages":
            pages = event.get("pages") if isinstance(event.get("pages"), list) else []
            text = json.dumps({"type": "pages", "pages": pages})
            session.latest_pages = text
            for viewer in list(session.viewers):
                viewer.post_pages(text)
        elif kind == "ready":
            session.headed = bool(event.get("headed"))
            if session.state == "starting" and session.stop_state is None:
                await self._set_state(session, "waiting_for_user")
        elif kind == "status":
            state = event.get("state")
            message = event.get("message") if isinstance(event.get("message"), str) else None
            if (
                state in HELPER_STATES
                and session.state in ("starting",) + HELPER_STATES
                and session.stop_state is None
            ):
                await self._set_state(session, state, message)
            elif message is not None and not session.ended:
                session.message = _clip(message)
                self._push_session(session)
        elif kind == "verified":
            session.verified = True
            await self._set_state(session, "verified", "Signed in. Closing the browser…")
        elif kind == "error":
            session.helper_error = {
                "kind": event.get("kind"),
                "message": _clip(event.get("message")),
                "holder": event.get("holder"),
            }
            if session.helper_error["message"] and not session.ended:
                session.message = session.helper_error["message"]
                self._push_session(session)
        elif kind == "closed":
            session.closed_reason = event.get("reason")

    async def _drain_stderr(self, session: LoginSession) -> None:
        """Keep the last lines for error messages. Never logged: it can echo page content."""
        stream = session.process.stderr
        while True:
            try:
                line = await stream.readline()
            except ValueError:
                continue
            if not line:
                return
            session.stderr_tail.append(
                line.decode("utf-8", "replace").rstrip()[:STDERR_LINE_MAX_CHARS],
            )

    # --- watchdog and stopping (review 7.3) ------------------------------------------

    async def _watchdog(self, session: LoginSession) -> None:
        loop = asyncio.get_running_loop()
        while session.helper_running and session.stop_state is None:
            await asyncio.sleep(self._watchdog_interval_s)
            if not session.helper_running or session.stop_state is not None:
                return
            silent_for = loop.time() - session.last_heartbeat
            if silent_for > self._heartbeat_timeout_s:
                logger.warning(
                    "login helper for %s sent no heartbeat for %.0f s; stopping it",
                    session.name, silent_for,
                )
                self._request_stop(
                    session, "failed",
                    f"The login helper stopped responding (no heartbeat for "
                    f"{self._heartbeat_timeout_s:.0f} s).",
                )
                return
            if datetime.now(timezone.utc) >= session.expires_at:
                logger.info("login session for %s reached its time limit; cancelling it", session.name)
                minutes = max(1, round((session.expires_at - session.started_at).total_seconds() / 60))
                self._request_stop(
                    session, "timed_out",
                    f"The login session reached its {minutes}-minute limit. Start again to retry.",
                )
                return

    def _request_stop(
        self,
        session: LoginSession,
        state: str,
        message: Optional[str],
        *,
        cancel_wait_s: Optional[float] = None,
        term_wait_s: Optional[float] = None,
    ) -> Optional[asyncio.Task]:
        """Start the stop sequence (once); the session ends in `state` after the helper exits."""
        if session.stop_state is None:
            session.stop_state = state
            session.stop_message = message
        if session.process is None:
            return None  # start() picks the request up once the helper is spawned
        if session.stop_task is None:
            session.stop_task = asyncio.create_task(self._stop_helper(
                session,
                self._cancel_wait_s if cancel_wait_s is None else cancel_wait_s,
                self._term_wait_s if term_wait_s is None else term_wait_s,
            ))
            session.tasks.add(session.stop_task)
            session.stop_task.add_done_callback(session.tasks.discard)
        return session.stop_task

    async def _stop_helper(self, session: LoginSession, cancel_wait_s: float, term_wait_s: float) -> None:
        """Steps 1-3: cancel, SIGTERM the group, SIGKILL the group; stop once the helper exits.

        Step 4 (reaping browsers that outlive the helper) runs in the lifecycle
        after every exit, not only after a stop.
        """
        proc = session.process
        if proc.returncode is not None:
            return
        await self._send_command(session, {"cmd": "cancel"})
        if await self._wait_exit(proc, cancel_wait_s):
            return
        logger.warning(
            "login helper for %s did not exit %.0f s after cancel; sending SIGTERM",
            session.name, cancel_wait_s,
        )
        self._signal_group(proc, signal.SIGTERM)
        if await self._wait_exit(proc, term_wait_s):
            return
        logger.warning(
            "login helper for %s did not exit %.0f s after SIGTERM; sending SIGKILL",
            session.name, term_wait_s,
        )
        self._signal_group(proc, signal.SIGKILL)
        await self._wait_exit(proc, term_wait_s)

    @staticmethod
    async def _wait_exit(proc: asyncio.subprocess.Process, timeout: float) -> bool:
        if proc.returncode is not None:
            return True
        try:
            await asyncio.wait_for(asyncio.shield(proc.wait()), timeout)
        except asyncio.TimeoutError:
            return False
        return True

    @staticmethod
    def _signal_group(proc: asyncio.subprocess.Process, sig: int) -> None:
        # start_new_session=True made the helper its group's leader. Until
        # asyncio has reaped it the pid can't be reused, so this can't hit a
        # stranger's group.
        if proc.returncode is not None:
            return
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    async def _reap_stragglers(self, session: LoginSession) -> None:
        """Step 4: SIGKILL browsers still running on the profile after the helper exited.

        Playwright starts Chromium in its own process group, so the group
        signals miss it. Only processes found right after the helper exited
        are candidates, so a scrape that takes the profile later is safe.
        """
        grace = SHUTDOWN_REAP_GRACE_S if self._shutting_down else self._reap_grace_s
        grace = min(grace, self._reap_grace_s)

        def scan() -> set[int]:
            return find_profile_processes(session.profile_dir, proc_root=self._proc_root)

        pids = await asyncio.to_thread(scan)
        if not pids:
            return
        logger.warning(
            "login session for %s: %d browser process(es) outlived the helper; "
            "killing any still running in %.0f s",
            session.name, len(pids), grace,
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + grace
        while pids and loop.time() < deadline:
            await asyncio.sleep(min(0.1, max(0.0, deadline - loop.time())))
            pids &= await asyncio.to_thread(scan)
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                continue
            logger.warning("login session for %s: killed browser process %d", session.name, pid)

    # --- lifecycle (review 7.5) ----------------------------------------------------------

    async def _lifecycle(self, session: LoginSession) -> None:
        name = session.name
        try:
            await session.process.wait()
            if session.reader_task is not None:
                # The last lines (verified, error, closed) may still be buffered.
                try:
                    await asyncio.wait_for(asyncio.shield(session.reader_task), READER_DRAIN_S)
                except asyncio.TimeoutError:
                    pass
            await self._reap_stragglers(session)
            logger.info(
                "login helper for %s exited with code %s (%s)",
                name, session.process.returncode, session.closed_reason or "no closed event",
            )
            await self._finish(session)
        except asyncio.CancelledError:
            self._end(session, "failed", "The coordinator shut down during the login session.")
            raise
        except Exception as e:  # noqa: BLE001 — a session must always end
            logger.exception("login session for %s failed", name)
            await self._set_state(session, "failed", f"The login session failed: {type(e).__name__}: {e}")
        finally:
            if not session.ended:
                self._end(session, "failed", "The login session ended unexpectedly.")
            for task in list(session.tasks):
                if task is not asyncio.current_task():
                    task.cancel()

    async def _finish(self, session: LoginSession) -> None:
        """Decide the outcome once the helper and its browser are gone."""
        reason = session.closed_reason
        if session.verified:
            await self._confirm(session, verified=True)
        elif session.stop_state is not None:
            await self._set_state(session, session.stop_state, session.stop_message)
        elif reason == "browser_closed":
            await self._set_state(
                session, "browser_closed",
                "The browser was closed. Running one scrape to check the sign-in…",
            )
            await self._confirm(session, verified=False)
        elif reason == "cancelled":
            await self._set_state(session, "cancelled", "The login session was cancelled.")
        elif reason == "timeout":
            await self._set_state(
                session, "timed_out", "The login session reached its time limit. Start again to retry.",
            )
        else:
            await self._set_state(session, "failed", self._failure_message(session))

    def _failure_message(self, session: LoginSession) -> str:
        error = session.helper_error or {}
        if error.get("message"):
            return error["message"]
        code = session.process.returncode if session.process is not None else None
        message = f"The login helper exited unexpectedly (exit code {code})."
        tail = [line for line in session.stderr_tail if line][-ERROR_TAIL_LINES:]
        if tail:
            message += " Last output: " + " | ".join(tail)
        return message

    async def _confirm(self, session: LoginSession, *, verified: bool) -> None:
        """One confirmation scrape; after a verified sign-in, mark the scraper ok first."""
        name = session.name
        if verified:
            await self._registry.mark_login_verified(name)
        if self._shutting_down:
            await self._set_state(
                session, "cancelled",
                "The coordinator shut down before the confirmation scrape; use Run now to confirm.",
            )
            return
        await self._set_state(session, "confirming", "Running one scrape to confirm the sign-in…")
        try:
            result = await self._registry.run(name, trigger="login_confirm")
        except Exception as e:  # noqa: BLE001
            logger.exception("confirmation scrape for %s raised", name)
            await self._set_state(
                session, "confirm_failed", f"The confirmation scrape failed: {type(e).__name__}: {e}",
            )
            return
        if result.success:
            rows = await asyncio.to_thread(_count_csv_rows, result.output_path)
            if rows is None:
                message = "Signed in, and the confirmation scrape succeeded."
            else:
                message = f"Signed in and scraped {rows} row{'s' if rows != 1 else ''}."
            await self._set_state(session, "succeeded", message)
            return
        error = _clip(result.error or "unknown error", 500)
        if result.error_kind in AUTH_ERROR_KINDS:
            what = "a login wall" if result.error_kind == "auth_required" else "the site's bot check"
            lead = (
                "Sign-in was verified, but the confirmation scrape still hit"
                if verified else
                "The browser was closed, but the confirmation scrape still hit"
            )
            message = f"{lead} {what}: {error}. Scheduled runs stay paused; try Re-login again."
        else:
            message = f"The confirmation scrape failed: {error}"
        await self._set_state(session, "confirm_failed", message)

    # --- shutdown ----------------------------------------------------------------------

    async def shutdown(self) -> None:
        """Stop every helper with shortened waits (coordinator lifespan exit)."""
        self._shutting_down = True
        stops = [
            self._request_stop(
                session, "cancelled", "The coordinator shut down.",
                cancel_wait_s=min(self._cancel_wait_s, SHUTDOWN_CANCEL_WAIT_S),
                term_wait_s=min(self._term_wait_s, SHUTDOWN_TERM_WAIT_S),
            )
            for session in list(self._sessions.values())
            if not session.ended and session.state != "confirming"
        ]
        stops = [task for task in stops if task is not None]
        if stops:
            logger.info("stopping %d login session(s) for shutdown", len(stops))
            await asyncio.gather(*stops, return_exceptions=True)
        lifecycles = list(self._lifecycles)
        if lifecycles:
            _, pending = await asyncio.wait(lifecycles, timeout=SHUTDOWN_LIFECYCLE_WAIT_S)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
