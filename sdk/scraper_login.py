"""Login helper: open a scraper's browser profile so a person can sign in again.

Runs inside the scraper package's venv, so it drives the scraper's own browser
library (the manifest's `auth.engine`, through its async API) and launches
Chromium exactly like the scrape does (`QuiltScraper.browser_launch_options`).
It never sees or stores a password: the person types into the real page.

Started as

    <venv python> -c "import sys; sys.path.insert(0, PKG); sys.path.append(ROOT); \\
                      from sdk.scraper_login import main; sys.exit(main())" \\
        --pkg-dir PKG --config-json CFG [--mode protocol|local] [--headless]

(`helper_command` builds that argv).

--mode protocol (the default; the coordinator's LoginSessionManager)
    JSON lines both ways. Events go out on the process's original stdout;
    fd 1 is pointed at stderr at startup so a stray print in the scraper
    package cannot corrupt the stream. Events: ready, frame, pages, status,
    verified, heartbeat (every 5 s), error, closed (always the last line).
    Commands come in on stdin: mouse, wheel, key, text, switch_page, history,
    navigate, check, cancel. EOF on stdin (the coordinator died) closes the
    browser and exits with closed(stdin_closed).

--mode local (scripts/scraper_login.py, for when the coordinator is down)
    No screencast and no JSON: status lines on stdout, a headed window,
    Enter runs the active check and q + Enter cancels.

In both modes the session holds the profile lock (role "login" or
"login-local") from before launch until ctx.close() returns, ends on its own
once the verify condition matches (checked every 2 s without navigating), and
gives up after auth.session_timeout_s. Completion never depends on noticing
the window close: if the close event never arrives, check, cancel and the
timeout still end the session.

--headless forces a headless browser. It exists for tests only.

Exit codes: 0 signed in (verified) or the person closed the browser,
2 ended without that (cancelled, timeout, stdin closed), 1 error,
3 the profile is held by another process.
"""
from __future__ import annotations

import argparse
import asyncio
import fnmatch
import importlib
import json
import math
import os
import queue
import signal
import sys
import tempfile
import threading
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, Callable, Optional, TextIO
from urllib.parse import urlsplit, urlunsplit

from sdk.scraper_auth import (
    AuthConfigError,
    ProfileBusy,
    ScraperAuth,
    parse_auth,
    profile_lock,
    resolve_profile_dir,
)

EXIT_DONE = 0
EXIT_ERROR = 1
EXIT_NOT_DONE = 2
EXIT_PROFILE_BUSY = 3

MODES = ("protocol", "local")
LOCK_ROLES = {"protocol": "login", "local": "login-local"}

HEARTBEAT_INTERVAL_S = 5.0
PASSIVE_CHECK_INTERVAL_S = 2.0
VERIFIED_SETTLE_S = 2.0          # let late cookies land before closing
CLOSE_TIMEOUT_S = 15.0           # ctx.close(): Chromium flushes the profile
NAVIGATION_TIMEOUT_MS = 30_000
CHECK_POLL_S = 0.25
# An active check stops early once the page has sat on another URL this long
# (the site redirected to its login page), instead of waiting out timeout_s.
CHECK_OFF_URL_GIVE_UP_S = 5.0

MAX_TEXT_CHARS = 256
MAX_COMMAND_BYTES = 8192
MAX_KEY_CHARS = 32
MAX_CODE_CHARS = 64
MAX_PAGE_ID_CHARS = 32
MAX_COORDINATE = 100_000.0
MAX_WHEEL_DELTA = 10_000.0
MAX_CLICK_COUNT = 3
MAX_DIALOG_MESSAGE_CHARS = 200

SCREENCAST_PARAMS = {"format": "jpeg", "quality": 70, "maxWidth": 1600, "maxHeight": 900}
FRAME_METADATA_KEYS = (
    "deviceWidth", "deviceHeight", "pageScaleFactor", "offsetTop",
    "scrollOffsetX", "scrollOffsetY", "timestamp",
)
DEFAULT_VIEWPORT = (1280, 720)

# Merged into <profile>/Default/Preferences before launch, so Chromium never
# offers to save the person's password into the scraper's profile.
PASSWORD_MANAGER_PREFS = {
    "credentials_enable_service": False,
    "profile": {"password_manager_enabled": False},
}

NOT_SIGNED_IN_MESSAGE = "Not signed in yet — finish signing in, then Check now."
WAITING_MESSAGE = "Sign in in the browser. This finishes by itself once you are signed in."

CLOSE_REASONS = ("verified", "cancelled", "timeout", "browser_closed", "stdin_closed", "error")
ERROR_KINDS = ("profile_busy", "engine_missing", "launch_failed", "internal")

# DOM KeyboardEvent.key values that Playwright's keyboard.down/up accept,
# besides single printable ASCII characters. Checked against Playwright 1.63;
# other named keys (Dead, Unidentified, F13, MediaStop, ...) raise
# "Unknown key" there, so they are dropped here.
NAMED_KEYS = frozenset({
    "Alt", "AltGraph", "CapsLock", "Control", "Meta", "NumLock", "ScrollLock", "Shift",
    "Enter", "Tab", "Backspace", "Delete", "Insert", "Escape",
    "ArrowDown", "ArrowLeft", "ArrowRight", "ArrowUp", "End", "Home", "PageDown", "PageUp",
    "ContextMenu", "Pause", "PrintScreen",
    "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12",
    "AudioVolumeMute", "AudioVolumeDown", "AudioVolumeUp",
    "MediaTrackNext", "MediaTrackPrevious", "MediaPlayPause",
})

_MOUSE_ACTIONS = ("move", "down", "up")
_MOUSE_BUTTONS = ("left", "middle", "right")
_KEY_ACTIONS = ("down", "up")
_HISTORY_ACTIONS = ("back", "forward", "reload")
_NAVIGATE_TARGETS = ("login", "verify")
_DEFAULT_PORTS = {"http": 80, "https": 443}


# ---------------------------------------------------------------------------
# Commands (stdin, protocol mode)
# ---------------------------------------------------------------------------

class CommandError(ValueError):
    """A command line that doesn't parse. The message never echoes the payload."""


@dataclass(frozen=True)
class Command:
    cmd: str
    args: dict = field(default_factory=dict)


def _field(obj: dict, name: str) -> Any:
    if name not in obj:
        raise CommandError(f"missing field {name!r}")
    return obj[name]


def _number(obj: dict, name: str, limit: float) -> float:
    value = _field(obj, name)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CommandError(f"{name} must be a finite number")
    if abs(value) > limit:
        raise CommandError(f"{name} is out of range")
    return float(value)


def _choice(obj: dict, name: str, choices: tuple[str, ...], default: Optional[str] = None) -> str:
    value = obj.get(name, default)
    if value not in choices:
        raise CommandError(f"{name} must be one of {', '.join(choices)}")
    return value


def _string(obj: dict, name: str, max_chars: int, *, required: bool = True) -> Optional[str]:
    """A non-empty string field; an optional one may also be absent, null or empty."""
    value = obj.get(name)
    if not required and (value is None or value == ""):
        return value
    if not isinstance(value, str) or not value:
        raise CommandError(f"{name} must be a non-empty string")
    if len(value) > max_chars:
        raise CommandError(f"{name} is longer than {max_chars} characters")
    return value


def parse_command(line: str | bytes) -> Command:
    """Validate one stdin line. Raises CommandError; unknown extra fields are ignored."""
    try:
        obj = json.loads(line)
    except (ValueError, UnicodeDecodeError):
        raise CommandError("not valid JSON") from None
    if not isinstance(obj, dict):
        raise CommandError("not a JSON object")
    cmd = obj.get("cmd")

    if cmd == "mouse":
        click_count = obj.get("click_count", 1)
        if (
            isinstance(click_count, bool)
            or not isinstance(click_count, int)
            or not 1 <= click_count <= MAX_CLICK_COUNT
        ):
            raise CommandError(f"click_count must be an integer from 1 to {MAX_CLICK_COUNT}")
        return Command("mouse", {
            "action": _choice(obj, "action", _MOUSE_ACTIONS),
            "x": _number(obj, "x", MAX_COORDINATE),
            "y": _number(obj, "y", MAX_COORDINATE),
            "button": _choice(obj, "button", _MOUSE_BUTTONS, "left"),
            "click_count": click_count,
        })
    if cmd == "wheel":
        return Command("wheel", {
            "x": _number(obj, "x", MAX_COORDINATE),
            "y": _number(obj, "y", MAX_COORDINATE),
            "dx": _number(obj, "dx", MAX_WHEEL_DELTA),
            "dy": _number(obj, "dy", MAX_WHEEL_DELTA),
        })
    if cmd == "key":
        return Command("key", {
            "action": _choice(obj, "action", _KEY_ACTIONS),
            "key": _string(obj, "key", MAX_KEY_CHARS),
            "code": _string(obj, "code", MAX_CODE_CHARS, required=False),
        })
    if cmd == "text":
        return Command("text", {"text": _string(obj, "text", MAX_TEXT_CHARS)})
    if cmd == "switch_page":
        return Command("switch_page", {"id": _string(obj, "id", MAX_PAGE_ID_CHARS)})
    if cmd == "history":
        return Command("history", {"action": _choice(obj, "action", _HISTORY_ACTIONS)})
    if cmd == "navigate":
        return Command("navigate", {"target": _choice(obj, "target", _NAVIGATE_TARGETS)})
    if cmd in ("check", "cancel"):
        return Command(cmd)
    raise CommandError("unknown cmd")


def classify_key(key: str) -> Optional[str]:
    """How to forward a DOM KeyboardEvent.key.

    "key": press it with keyboard.down/up (Playwright's layout knows it).
    "text": a character the layout doesn't know (é, ß, emoji); insert it as
        text on key down and do nothing on key up.
    None: ignore it (a named key Playwright can't press, or a control char).
    """
    if key in NAMED_KEYS:
        return "key"
    if len(key) == 1:
        if " " <= key <= "~":
            return "key"
        return "text" if key.isprintable() else None
    # Several code points: an emoji sequence (skin tone, flag, ZWJ family) is
    # text; an ASCII word is a named key Playwright doesn't have.
    if key.isascii():
        return None
    if any(ord(c) < 0x20 or 0x7F <= ord(c) < 0xA0 for c in key):
        return None
    return "text"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# ---------------------------------------------------------------------------
# Verify: URL matching
# ---------------------------------------------------------------------------

def _url_key(url: str) -> Optional[tuple]:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS or not parts.hostname:
        return None
    return (scheme, parts.hostname.lower(), port or _DEFAULT_PORTS[scheme], parts.path.rstrip("/"))


def url_matches(url: str, expected: str) -> bool:
    """Whether a page URL counts as auth.verify.url.

    Same scheme, host (and port) and path, ignoring the query, the fragment
    and a trailing slash. When `expected` contains `*` it is an fnmatch
    pattern, matched against the URL without its query (or fragment).
    """
    if not url or not expected:
        return False
    if "*" in expected:
        try:
            parts = urlsplit(url)
        except ValueError:
            return False
        bare = urlunsplit(parts._replace(query="", fragment=""))
        return fnmatch.fnmatchcase(bare, expected)
    actual_key = _url_key(url)
    return actual_key is not None and actual_key == _url_key(expected)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

class PageTracker:
    """Open pages in the order they opened, and which one is active.

    The newest page becomes active when it opens. When the active page
    closes, the most recently opened page still open takes over. A person
    can switch to any open page.
    """

    def __init__(self) -> None:
        self._order: list[str] = []
        self.active: Optional[str] = None

    @property
    def ids(self) -> list[str]:
        return list(self._order)

    def __contains__(self, page_id: str) -> bool:
        return page_id in self._order

    def opened(self, page_id: str) -> str:
        if page_id not in self._order:
            self._order.append(page_id)
        self.active = page_id
        return page_id

    def closed(self, page_id: str) -> Optional[str]:
        """Forget a page; return the active page afterwards (None when none are left)."""
        if page_id in self._order:
            self._order.remove(page_id)
        if self.active == page_id or self.active not in self._order:
            self.active = self._order[-1] if self._order else None
        return self.active

    def switch(self, page_id: str) -> bool:
        if page_id not in self._order:
            return False
        self.active = page_id
        return True


# ---------------------------------------------------------------------------
# Profile preparation
# ---------------------------------------------------------------------------

def _deep_merge(target: dict, updates: dict) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        elif isinstance(value, dict):
            target[key] = json.loads(json.dumps(value))
        else:
            target[key] = value


def disable_password_manager(profile_dir: str) -> str:
    """Merge PASSWORD_MANAGER_PREFS into <profile>/Default/Preferences; return its path.

    Creates the file when it is missing. Raises on an unreadable or
    non-object Preferences file rather than overwriting the profile's
    settings; the caller logs a warning and continues.
    """
    path = os.path.join(profile_dir, "Default", "Preferences")
    prefs: dict = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            prefs = json.load(f)
        if not isinstance(prefs, dict):
            raise ValueError(f"{path} is not a JSON object")
    _deep_merge(prefs, PASSWORD_MANAGER_PREFS)
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".Preferences-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(prefs, f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


# ---------------------------------------------------------------------------
# Output: JSON lines (protocol) or status lines (local)
# ---------------------------------------------------------------------------

def take_protocol_fd() -> int:
    """Keep the real stdout for protocol events and point fd 1 at stderr.

    Returns a duplicate of the original fd 1. Anything else that writes to
    stdout afterwards (a print in the scraper package, a child process
    inheriting fd 1) lands on stderr instead of in the protocol stream.
    """
    sys.stdout.flush()
    proto_fd = os.dup(1)
    os.dup2(2, 1)
    return proto_fd


class ProtocolOutput:
    """Writes JSON-line events from a dedicated thread, in order.

    The event loop never blocks on a full pipe. `send` takes an optional
    callback that runs once the line is written (or dropped because the pipe
    is gone); screencast frames are acknowledged from it, so Chromium's frame
    rate follows how fast the reader drains the pipe. The thread writes to
    the raw fd, so a write stuck on a dead reader never holds a Python-level
    stream lock at interpreter exit.
    """

    local = False

    def __init__(self, fd: int) -> None:
        self._fd = fd
        self._queue: "queue.Queue[Optional[tuple[bytes, Optional[Callable[[], None]]]]]" = queue.Queue()
        self._broken = False
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="login-protocol-writer", daemon=True)
        self._thread.start()

    def send(self, event: dict, on_written: Optional[Callable[[], None]] = None) -> None:
        if self._closed:
            if on_written is not None:
                on_written()
            return
        line = json.dumps(event, separators=(",", ":"), ensure_ascii=False) + "\n"
        self._queue.put((line.encode("utf-8"), on_written))

    def _write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            written = os.write(self._fd, view)
            view = view[written:]

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            data, on_written = item
            if not self._broken:
                try:
                    self._write(data)
                except OSError:
                    # The reader is gone (coordinator died); stdin EOF ends the session.
                    self._broken = True
            if on_written is not None:
                try:
                    on_written()
                except Exception:  # noqa: BLE001 — a closed loop must not kill the writer
                    pass

    def close(self, timeout: float = 5.0) -> None:
        """Flush what is queued (waiting up to `timeout`) and stop the thread."""
        if self._closed:
            return
        self._closed = True
        self._queue.put(None)
        self._thread.join(timeout)


class LocalOutput:
    """Human status lines for the terminal fallback."""

    local = True
    PREFIX = "[scraper-login]"

    def __init__(self, stream: TextIO, *, session_timeout_s: Optional[int] = None) -> None:
        self._stream = stream
        self._active_page: Optional[str] = None
        self.session_timeout_s = session_timeout_s

    def _print(self, text: str) -> None:
        try:
            print(f"{self.PREFIX} {text}", file=self._stream, flush=True)
        except (OSError, ValueError):
            pass

    def send(self, event: dict, on_written: Optional[Callable[[], None]] = None) -> None:
        kind = event.get("type")
        if kind == "ready":
            window = "A Chromium window is open" if event.get("headed") else "Chromium is running headless"
            limit = ""
            if self.session_timeout_s:
                limit = f" The session ends after {max(1, self.session_timeout_s // 60)} min."
            self._print(
                f"{window}. Sign in there; this finishes by itself once you are signed in.{limit}\n"
                f"{self.PREFIX} Press Enter to check now, or q + Enter to cancel."
            )
        elif kind == "status":
            self._print(event.get("message") or event.get("state", ""))
        elif kind == "pages":
            active = next((p for p in event.get("pages", []) if p.get("active")), None)
            if active and active.get("id") != self._active_page:
                self._active_page = active.get("id")
                self._print(f"Active tab: {active.get('title') or active.get('url')}")
        elif kind == "verified":
            self._print(f"Signed in ({event.get('url')}). Closing the browser so the profile is saved…")
        elif kind == "error":
            self._print(f"Error ({event.get('kind')}): {event.get('message')}")
        elif kind == "closed":
            self._print(f"Login session ended: {event.get('reason')}.")
        if on_written is not None:
            on_written()

    def close(self, timeout: float = 5.0) -> None:
        pass


# ---------------------------------------------------------------------------
# Preparing the session (no browser yet)
# ---------------------------------------------------------------------------

class HelperError(Exception):
    """A fatal error reported as an `error` event."""

    def __init__(self, kind: str, message: str, holder: Optional[dict] = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.holder = holder


@dataclass
class SessionPlan:
    auth: ScraperAuth
    profile_dir: str
    launch_options: dict
    headed: bool
    async_api: Any
    mode: str = "protocol"

    @property
    def role(self) -> str:
        return LOCK_ROLES[self.mode]


def _runs_headed(manifest: dict, config: dict) -> bool:
    # The rule scrape runs use, so the login browser launches the same way.
    # sdk can't import the coordinator at module level; the bootstrap puts the
    # quilt root on sys.path.
    from coordinator.services.scraper_engine import runs_headed

    return runs_headed(manifest, config)


def import_engine(engine: str) -> ModuleType:
    """`<engine>.async_api` from the package venv, or HelperError(engine_missing)."""
    try:
        return importlib.import_module(f"{engine}.async_api")
    except ImportError as e:
        raise HelperError(
            "engine_missing",
            f"the scraper's venv cannot import {engine}.async_api (auth.engine: {engine}): {e}. "
            f"Install it there: {sys.executable} -m pip install {engine} && "
            f"{sys.executable} -m {engine} install chromium",
        ) from None


def scraper_launch_options(pkg_dir: str, manifest: dict, config: dict) -> dict:
    """on_start(config) -> browser_launch_options() -> on_stop(); never on_run."""
    from sdk.scraper_runner import _load_scraper

    scraper = _load_scraper(pkg_dir, manifest)
    scraper.on_start(config)
    try:
        hook = getattr(scraper, "browser_launch_options", None)
        options = hook() if callable(hook) else {}
    finally:
        scraper.on_stop()
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise TypeError(f"browser_launch_options() must return a dict, got {type(options).__name__}")
    # The helper owns these two.
    return {k: v for k, v in options.items() if k not in ("user_data_dir", "headless")}


def prepare(pkg_dir: str, config: dict, mode: str, headless_override: bool) -> SessionPlan:
    from sdk.scraper_runner import _load_manifest

    try:
        manifest = _load_manifest(pkg_dir)
    except Exception as e:  # noqa: BLE001
        raise HelperError("internal", f"cannot read {pkg_dir}/quilt.yaml: {e}") from None
    try:
        auth = parse_auth(manifest)
        if auth is None:
            raise AuthConfigError("the scraper's quilt.yaml has no auth: block")
        if auth.kind != "browser_profile":
            raise AuthConfigError(f"auth.kind {auth.kind!r} has no browser login")
        profile_dir = resolve_profile_dir(manifest, config, base_dir=pkg_dir)
    except AuthConfigError as e:
        raise HelperError("internal", str(e)) from None

    async_api = import_engine(auth.engine)

    try:
        launch_options = scraper_launch_options(pkg_dir, manifest, config)
    except Exception as e:  # noqa: BLE001
        raise HelperError(
            "launch_failed",
            f"the scraper's browser_launch_options() failed: {type(e).__name__}: {e}",
        ) from None

    if headless_override:
        headed = False
    elif mode == "local":
        # No screencast in local mode: the window is the only way to sign in.
        headed = True
    else:
        headed = _runs_headed(manifest, config)
    return SessionPlan(
        auth=auth, profile_dir=profile_dir, launch_options=launch_options,
        headed=headed, async_api=async_api, mode=mode,
    )


# ---------------------------------------------------------------------------
# The session
# ---------------------------------------------------------------------------

def exit_code(reason: str) -> int:
    """Process exit code for a close reason (see the module docstring)."""
    if reason in ("verified", "browser_closed"):
        return EXIT_DONE
    if reason == "error":
        return EXIT_ERROR
    return EXIT_NOT_DONE


def _warn(message: str) -> None:
    print(f"scraper_login: {message}", file=sys.stderr, flush=True)


class LoginSession:
    """Drives one login: lock, launch, input, verify, close.

    `run()` returns (close_reason, exit_code); the caller sends `closed`
    once the lock is released and the browser is gone.
    """

    heartbeat_interval_s = HEARTBEAT_INTERVAL_S
    passive_check_interval_s = PASSIVE_CHECK_INTERVAL_S
    verified_settle_s = VERIFIED_SETTLE_S
    close_timeout_s = CLOSE_TIMEOUT_S
    navigation_timeout_ms = NAVIGATION_TIMEOUT_MS

    def __init__(self, plan: SessionPlan, out: Any, stdin_fd: Optional[int] = None) -> None:
        self._plan = plan
        self._auth = plan.auth
        self._out = out
        self._stdin_fd = stdin_fd
        self._local = plan.mode == "local"
        self._tracker = PageTracker()
        self._pages: dict[str, Any] = {}
        self._page_seq = 0
        self._state = "waiting_for_user"   # status states: waiting_for_user | checking
        self._ready = False
        self._viewport = DEFAULT_VIEWPORT
        self._cdp: Any = None
        self._cdp_page_id: Optional[str] = None
        self._tasks: set[asyncio.Task] = set()
        self._check_task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._done: Optional[asyncio.Future] = None
        self._inputs: Optional[asyncio.Queue] = None
        self._pages_dirty: Optional[asyncio.Event] = None
        self._screencast_lock: Optional[asyncio.Lock] = None

    # -- plumbing ----------------------------------------------------------

    def _send(self, event: dict, on_written: Optional[Callable[[], None]] = None) -> None:
        self._out.send(event, on_written)

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            exc = task.exception()
            _warn(f"background task failed: {type(exc).__name__}")

    def finish(self, reason: str) -> None:
        if self._done is not None and not self._done.done():
            self._done.set_result(reason)

    @property
    def finished(self) -> bool:
        return self._done is not None and self._done.done()

    def _status(self, message: str) -> None:
        self._send({"type": "status", "state": self._state, "message": message})

    def _set_state(self, state: str, message: str) -> None:
        self._state = state
        self._status(message)

    def _error(self, kind: str, message: str, holder: Optional[dict] = None) -> None:
        event = {"type": "error", "kind": kind, "message": message}
        if holder is not None:
            event["holder"] = holder
        self._send(event)

    # -- lifecycle ---------------------------------------------------------

    async def run(self) -> tuple[str, int]:
        loop = asyncio.get_running_loop()
        self._loop = loop
        self._done = loop.create_future()
        self._inputs = asyncio.Queue()
        self._pages_dirty = asyncio.Event()
        self._screencast_lock = asyncio.Lock()
        installed = self._install_signal_handlers(loop)
        if not self._local:
            self._spawn(self._heartbeat_loop())
        self._spawn(self._timeout_watch(self._auth.session_timeout_s))
        self._spawn(self._input_worker())
        if self._stdin_fd is not None:
            self._start_stdin_reader(loop, self._stdin_fd)
        try:
            return await self._run_locked()
        finally:
            for sig in installed:
                loop.remove_signal_handler(sig)
            for task in list(self._tasks):
                task.cancel()
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)

    def _install_signal_handlers(self, loop: asyncio.AbstractEventLoop) -> list:
        installed = []
        if threading.current_thread() is not threading.main_thread():
            return installed
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self.finish, "cancelled")
                installed.append(sig)
            except (NotImplementedError, RuntimeError, ValueError):
                pass
        return installed

    async def _run_locked(self) -> tuple[str, int]:
        try:
            with profile_lock(self._plan.profile_dir, self._plan.role):
                return await self._run_with_profile()
        except ProfileBusy as e:
            self._error("profile_busy", str(e), holder=e.holder)
            return "error", EXIT_PROFILE_BUSY

    async def _run_with_profile(self) -> tuple[str, int]:
        try:
            disable_password_manager(self._plan.profile_dir)
        except Exception as e:  # noqa: BLE001 — never stop a login over it
            _warn(f"could not turn off the password manager in the profile: {type(e).__name__}: {e}")

        reason: Optional[str] = None
        try:
            async with self._plan.async_api.async_playwright() as pw:
                try:
                    ctx = await pw.chromium.launch_persistent_context(
                        self._plan.profile_dir,
                        headless=not self._plan.headed,
                        **self._plan.launch_options,
                    )
                except Exception as e:  # noqa: BLE001
                    reason = "error"
                    self._error("launch_failed", f"the browser did not start: {type(e).__name__}: {e}")
                    return reason, EXIT_ERROR
                try:
                    reason = await self._drive(ctx)
                except Exception as e:  # noqa: BLE001
                    reason = "error"
                    self._error("internal", f"{type(e).__name__}: {e}")
                finally:
                    await self._close_context(ctx)
        except Exception as e:  # noqa: BLE001 — the driver failed to start, or to stop
            if reason is None:
                self._error("launch_failed", f"the browser driver did not start: {type(e).__name__}: {e}")
                return "error", EXIT_ERROR
            _warn(f"the browser driver did not stop cleanly: {type(e).__name__}")
        return reason, exit_code(reason)

    async def _close_context(self, ctx: Any) -> None:
        try:
            await asyncio.wait_for(self._stop_screencast(), 5)
        except Exception:  # noqa: BLE001 — closing the context ends it anyway
            pass
        try:
            await asyncio.wait_for(ctx.close(), self.close_timeout_s)
        except asyncio.TimeoutError:
            _warn(f"the browser did not close within {self.close_timeout_s:.0f} s")
        except Exception:  # noqa: BLE001 — already closed
            pass

    async def _drive(self, ctx: Any) -> str:
        ctx.on("page", self._on_new_page)
        ctx.on("close", lambda *_: self.finish("browser_closed"))
        for page in list(ctx.pages):
            self._track(page)
        if self._tracker.active is None:
            self._track(await ctx.new_page())

        page = self._active_page()
        goto = self._spawn(self._open(page, self._auth.login_url))
        await asyncio.wait({goto, self._done}, return_when=asyncio.FIRST_COMPLETED)
        if self.finished:
            return self._done.result()
        load_error = goto.result()

        self._viewport = await self._measure_viewport(self._active_page())
        self._ready = True
        self._send({
            "type": "ready",
            "viewport": {"width": self._viewport[0], "height": self._viewport[1]},
            "headed": self._plan.headed,
        })
        self._spawn(self._pages_emitter())
        self._pages_changed()
        if not self._local:
            await self._sync_screencast()
        self._set_state("waiting_for_user", WAITING_MESSAGE)
        if load_error:
            self._status(load_error)
        self._spawn(self._passive_check_loop())

        reason = await self._done
        if reason == "verified":
            await asyncio.sleep(self.verified_settle_s)
        return reason

    async def _open(self, page: Any, url: str) -> Optional[str]:
        """Navigate, tolerating errors; returns a status message on failure."""
        try:
            await page.goto(url, timeout=self.navigation_timeout_ms, wait_until="domcontentloaded")
        except Exception as e:  # noqa: BLE001 — a challenge or slow page is fine
            return (
                f"The page did not finish loading ({type(e).__name__}); "
                "it may still be loading or showing a challenge."
            )
        return None

    # -- background loops ----------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        while True:
            self._send({"type": "heartbeat"})
            await asyncio.sleep(self.heartbeat_interval_s)

    async def _timeout_watch(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        if not self.finished:
            self._status(f"The login session reached its {int(seconds)} s limit.")
        self.finish("timeout")

    async def _passive_check_loop(self) -> None:
        while not self.finished:
            await asyncio.sleep(self.passive_check_interval_s)
            if self.finished or self._state == "checking":
                continue
            page = self._active_page()
            if page is not None and await self._signed_in(page):
                self._verified(page.url)
                return

    # -- verify ----------------------------------------------------------------

    async def _signed_in(self, page: Any) -> bool:
        """The verify condition: URL (if given) and any visible selector (if given)."""
        try:
            if page.is_closed():
                return False
            verify = self._auth.verify
            if verify.url and not url_matches(page.url, verify.url):
                return False
            if not verify.selectors:
                return True
            for selector in verify.selectors:
                try:
                    if await page.locator(selector).first.is_visible():
                        return True
                except Exception:  # noqa: BLE001 — navigating, or a bad selector
                    continue
        except Exception:  # noqa: BLE001
            return False
        return False

    def _verified(self, url: str) -> None:
        if self.finished:
            return
        self._send({"type": "verified", "url": url})
        self.finish("verified")

    def request_check(self) -> None:
        if not self._ready or self.finished:
            self._status("The browser is still starting; try again in a moment.")
            return
        if not self._auth.verify.url:
            self._status("Check now needs auth.verify.url; this scraper only has a selector.")
            return
        if self._check_task is not None and not self._check_task.done():
            self._status("A check is already running.")
            return
        self._check_task = self._spawn(self._active_check())

    async def _active_check(self) -> None:
        page = self._active_page()
        if page is None:
            return
        verify = self._auth.verify
        loop = asyncio.get_running_loop()
        self._set_state("checking", "Checking whether you are signed in…")
        deadline = loop.time() + verify.timeout_s
        try:
            await page.goto(verify.url, timeout=verify.timeout_s * 1000, wait_until="domcontentloaded")
        except Exception:  # noqa: BLE001 — a timeout or challenge still gets a look
            pass
        off_url_since: Optional[float] = None
        while not self.finished:
            if await self._signed_in(page):
                self._verified(page.url)
                return
            now = loop.time()
            if now >= deadline or page.is_closed():
                break
            if url_matches(page.url, verify.url):
                off_url_since = None
            elif off_url_since is None:
                off_url_since = now
            elif now - off_url_since >= CHECK_OFF_URL_GIVE_UP_S:
                break
            await asyncio.sleep(CHECK_POLL_S)
        if not self.finished:
            self._set_state("waiting_for_user", NOT_SIGNED_IN_MESSAGE)

    # -- pages -------------------------------------------------------------------

    def _page_id(self, page: Any) -> Optional[str]:
        for page_id, known in self._pages.items():
            if known is page:
                return page_id
        return None

    def _active_page(self) -> Any:
        active = self._tracker.active
        return self._pages.get(active) if active is not None else None

    def _track(self, page: Any) -> str:
        existing = self._page_id(page)
        if existing is not None:
            return existing
        self._page_seq += 1
        page_id = f"p{self._page_seq}"
        self._pages[page_id] = page
        self._tracker.opened(page_id)
        page.on("close", lambda *_: self._on_page_closed(page_id))
        page.on("framenavigated", lambda frame: self._on_navigated(page, frame))
        page.on("load", lambda *_: self._pages_changed())
        page.on("dialog", lambda dialog: self._spawn(self._on_dialog(dialog)))
        return page_id

    def _on_new_page(self, page: Any) -> None:
        if self.finished:
            return
        self._track(page)
        self._active_changed()

    def _on_navigated(self, page: Any, frame: Any) -> None:
        try:
            main = frame == page.main_frame
        except Exception:  # noqa: BLE001
            main = True
        if main:
            self._pages_changed()

    def _on_page_closed(self, page_id: str) -> None:
        self._pages.pop(page_id, None)
        was_active = self._tracker.active == page_id
        self._tracker.closed(page_id)
        if not self._tracker.ids:
            # Nothing left to show or drive: the person closed the browser.
            self.finish("browser_closed")
            return
        if was_active:
            self._active_changed()
        else:
            self._pages_changed()

    def switch_page(self, page_id: str) -> None:
        if not self._tracker.switch(page_id):
            self._status("That tab is no longer open.")
            self._pages_changed()
            return
        self._active_changed()

    def _active_changed(self) -> None:
        self._pages_changed()
        if self._ready and not self._local:
            self._spawn(self._sync_screencast())

    def _pages_changed(self) -> None:
        if self._pages_dirty is not None:
            self._pages_dirty.set()

    async def _pages_emitter(self) -> None:
        while True:
            await self._pages_dirty.wait()
            await asyncio.sleep(0.1)  # coalesce bursts (open + navigate + load)
            self._pages_dirty.clear()
            entries = []
            for page_id in self._tracker.ids:
                page = self._pages.get(page_id)
                if page is None:
                    continue
                try:
                    title = await asyncio.wait_for(page.title(), 2)
                except Exception:  # noqa: BLE001 — navigating or closing
                    title = ""
                entries.append({
                    "id": page_id, "url": page.url, "title": title,
                    "active": page_id == self._tracker.active,
                })
            self._send({"type": "pages", "pages": entries})

    async def _on_dialog(self, dialog: Any) -> None:
        kind = getattr(dialog, "type", "dialog")
        try:
            if kind == "beforeunload":
                await dialog.accept()
                verb = "Accepted"
            else:
                await dialog.dismiss()
                verb = "Dismissed"
        except Exception:  # noqa: BLE001 — the page went away
            return
        message = f"{verb} a {kind} dialog"
        text = (getattr(dialog, "message", "") or "").strip()
        if text:
            message += f": {text[:MAX_DIALOG_MESSAGE_CHARS]}"
        self._status(message)

    async def _measure_viewport(self, page: Any) -> tuple[int, int]:
        if page is None:
            return DEFAULT_VIEWPORT
        size = getattr(page, "viewport_size", None)
        if isinstance(size, dict) and size.get("width") and size.get("height"):
            return int(size["width"]), int(size["height"])
        try:
            width, height = await page.evaluate("() => [window.innerWidth, window.innerHeight]")
            if width and height:
                return int(width), int(height)
        except Exception:  # noqa: BLE001
            pass
        return DEFAULT_VIEWPORT

    # -- screencast ------------------------------------------------------------

    async def _sync_screencast(self) -> None:
        """Point the screencast at the active page (stop the old one first)."""
        async with self._screencast_lock:
            while not self.finished and self._cdp_page_id != self._tracker.active:
                await self._stop_screencast_locked()
                page_id = self._tracker.active
                page = self._pages.get(page_id) if page_id else None
                if page is None or page.is_closed():
                    return
                self._viewport = await self._measure_viewport(page)
                try:
                    cdp = await page.context.new_cdp_session(page)
                    self._cdp, self._cdp_page_id = cdp, page_id
                    cdp.on("Page.screencastFrame", lambda params, cdp=cdp: self._on_frame(cdp, params))
                    await cdp.send("Page.startScreencast", SCREENCAST_PARAMS)
                except Exception as e:  # noqa: BLE001 — the page closed meanwhile
                    _warn(f"screencast did not start: {type(e).__name__}")
                    self._cdp, self._cdp_page_id = None, None
                    return

    async def _stop_screencast(self) -> None:
        if self._screencast_lock is None:
            return
        async with self._screencast_lock:
            await self._stop_screencast_locked()

    async def _stop_screencast_locked(self) -> None:
        cdp, self._cdp, self._cdp_page_id = self._cdp, None, None
        if cdp is None:
            return
        for step in (lambda: cdp.send("Page.stopScreencast"), cdp.detach):
            try:
                await asyncio.wait_for(step(), 5)
            except Exception:  # noqa: BLE001 — the page is gone already
                pass

    def _on_frame(self, cdp: Any, params: dict) -> None:
        if cdp is not self._cdp:
            return  # a frame from a screencast we already stopped
        self._spawn(self._forward_frame(cdp, params))

    async def _forward_frame(self, cdp: Any, params: dict) -> None:
        metadata = params.get("metadata") or {}
        width, height = metadata.get("deviceWidth"), metadata.get("deviceHeight")
        if isinstance(width, (int, float)) and isinstance(height, (int, float)) and width > 0 and height > 0:
            self._viewport = (width, height)
        loop = asyncio.get_running_loop()
        written = loop.create_future()

        def on_written() -> None:
            try:
                loop.call_soon_threadsafe(lambda: written.done() or written.set_result(None))
            except RuntimeError:
                pass  # the loop already closed

        self._send({
            "type": "frame",
            "data": params.get("data", ""),
            "metadata": {k: metadata[k] for k in FRAME_METADATA_KEYS if k in metadata},
        }, on_written)
        await written
        try:
            await cdp.send("Page.screencastFrameAck", {"sessionId": params.get("sessionId")})
        except Exception:  # noqa: BLE001 — the session was detached
            pass

    # -- input -------------------------------------------------------------------

    def handle_command(self, command: Command) -> None:
        """Route one validated command. Input is applied in order by one worker;
        navigation and checks run beside it, so cancel always gets through."""
        if command.cmd == "cancel":
            self.finish("cancelled")
        elif command.cmd == "check":
            self.request_check()
        elif command.cmd in ("history", "navigate"):
            if not self._ready or self.finished:
                return
            self._spawn(self._navigation(command))
        elif self._inputs is not None:
            self._inputs.put_nowait(command)

    async def _input_worker(self) -> None:
        while True:
            command = await self._inputs.get()
            if not self._ready or self.finished:
                continue
            if command.cmd == "switch_page":
                self.switch_page(command.args["id"])
                continue
            page = self._active_page()
            if page is None:
                continue
            try:
                await self.apply_input(page, command)
            except Exception as e:  # noqa: BLE001 — page navigating or closed
                # Only the type: input payloads (keys, text) never reach a log.
                _warn(f"{command.cmd} input was not applied: {type(e).__name__}")

    async def apply_input(self, page: Any, command: Command) -> None:
        args = command.args
        width, height = self._viewport
        if command.cmd in ("mouse", "wheel"):
            x = clamp(args["x"], 0, max(0, width - 1))
            y = clamp(args["y"], 0, max(0, height - 1))
            await page.mouse.move(x, y)
            if command.cmd == "wheel":
                await page.mouse.wheel(args["dx"], args["dy"])
            elif args["action"] == "down":
                await page.mouse.down(button=args["button"], click_count=args["click_count"])
            elif args["action"] == "up":
                await page.mouse.up(button=args["button"], click_count=args["click_count"])
        elif command.cmd == "key":
            key = args["key"]
            kind = classify_key(key)
            if kind == "key":
                if args["action"] == "down":
                    await page.keyboard.down(key)
                else:
                    await page.keyboard.up(key)
            elif kind == "text" and args["action"] == "down":
                await page.keyboard.insert_text(key)
        elif command.cmd == "text":
            await page.keyboard.insert_text(args["text"])

    async def _navigation(self, command: Command) -> None:
        page = self._active_page()
        if page is None:
            return
        timeout = self.navigation_timeout_ms
        try:
            if command.cmd == "navigate":
                if command.args["target"] == "verify":
                    if not self._auth.verify.url:
                        self._status("This scraper has no verify URL to go to.")
                        return
                    url = self._auth.verify.url
                else:
                    url = self._auth.login_url
                await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            elif command.args["action"] == "back":
                await page.go_back(timeout=timeout)
            elif command.args["action"] == "forward":
                await page.go_forward(timeout=timeout)
            else:
                await page.reload(timeout=timeout)
        except Exception as e:  # noqa: BLE001 — slow page or a challenge; the person sees it
            if not self.finished:
                self._status(f"Navigation did not finish ({type(e).__name__}).")

    def _handle_local_line(self, line: str) -> None:
        word = line.strip().lower()
        if word == "":
            self.request_check()
        elif word in ("q", "quit", "cancel"):
            self.finish("cancelled")
        else:
            self._status("Press Enter to check now, or q + Enter to cancel.")

    def _handle_protocol_line(self, raw: Optional[bytes]) -> None:
        if raw is None:
            self._status(f"Ignored a command longer than {MAX_COMMAND_BYTES} bytes.")
            return
        if not raw.strip():
            return
        try:
            command = parse_command(raw)
        except CommandError as e:
            self._status(f"Ignored an invalid command: {e}.")
            return
        self.handle_command(command)

    def _on_stdin_line(self, raw: Optional[bytes]) -> None:
        if self._local:
            if raw is not None:
                self._handle_local_line(raw.decode("utf-8", "replace"))
        else:
            self._handle_protocol_line(raw)

    def _on_stdin_eof(self) -> None:
        if self._local:
            return  # the keyboard went away; the window, check and timeout remain
        self.finish("stdin_closed")

    def _start_stdin_reader(self, loop: asyncio.AbstractEventLoop, fd: int) -> None:
        """Read stdin lines on a daemon thread.

        It reads the raw fd (os.read), not sys.stdin: a daemon thread blocked
        inside a buffered reader's lock can abort interpreter shutdown.
        """

        def deliver(callback: Callable, *args: Any) -> bool:
            try:
                loop.call_soon_threadsafe(callback, *args)
                return True
            except RuntimeError:
                return False  # the loop is closed

        def read() -> None:
            buf = b""
            discarding = False  # inside an oversize line, until its newline
            while True:
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    chunk = b""
                if not chunk:
                    if buf and not discarding:
                        deliver(self._on_stdin_line, buf)
                    deliver(self._on_stdin_eof)
                    return
                buf += chunk
                while True:
                    newline = buf.find(b"\n")
                    if newline < 0:
                        if len(buf) > MAX_COMMAND_BYTES:
                            if not discarding and not deliver(self._on_stdin_line, None):
                                return
                            discarding = True
                            buf = b""
                        break
                    line, buf = buf[:newline + 1], buf[newline + 1:]
                    if discarding:
                        discarding = False
                        continue
                    if len(line) > MAX_COMMAND_BYTES + 1:
                        line = None
                    if not deliver(self._on_stdin_line, line):
                        return

        threading.Thread(target=read, name="login-stdin-reader", daemon=True).start()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def helper_command(
    python: str,
    pkg_dir: str,
    quilt_root: str,
    config: Optional[dict] = None,
    *,
    mode: str = "protocol",
    headless: bool = False,
) -> list[str]:
    """argv that runs this helper in the package's interpreter (mirrors the scrape runner)."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    bootstrap = (
        f"import sys; sys.path.insert(0, {pkg_dir!r}); sys.path.append({quilt_root!r}); "
        f"from sdk.scraper_login import main; sys.exit(main())"
    )
    argv = [
        python, "-c", bootstrap,
        "--pkg-dir", pkg_dir,
        "--config-json", json.dumps(config or {}),
        "--mode", mode,
    ]
    if headless:
        argv.append("--headless")
    return argv


def _parse_args(argv: Optional[list[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="sdk.scraper_login")
    parser.add_argument("--pkg-dir", required=True)
    parser.add_argument("--config-json", default="{}")
    parser.add_argument("--mode", choices=MODES, default="protocol")
    parser.add_argument("--headless", action="store_true",
                        help="force a headless browser (tests only)")
    return parser.parse_args(argv)


async def _amain(args: argparse.Namespace, out: Any, stdin_fd: Optional[int]) -> tuple[str, int]:
    try:
        try:
            config = json.loads(args.config_json)
        except ValueError as e:
            raise HelperError("internal", f"--config-json is not valid JSON: {e}") from None
        if not isinstance(config, dict):
            raise HelperError("internal", "--config-json must be a JSON object")
        plan = prepare(os.path.abspath(args.pkg_dir), config, args.mode, args.headless)
    except HelperError as e:
        out.send({"type": "error", "kind": e.kind, "message": e.message})
        return "error", EXIT_ERROR
    if isinstance(out, LocalOutput):
        out.session_timeout_s = plan.auth.session_timeout_s
    return await LoginSession(plan, out, stdin_fd).run()


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.mode == "protocol":
        out: Any = ProtocolOutput(take_protocol_fd())
    else:
        out = LocalOutput(sys.stdout)
    try:
        stdin_fd: Optional[int] = sys.stdin.fileno()
    except (AttributeError, OSError, ValueError):
        stdin_fd = None
    try:
        try:
            reason, code = asyncio.run(_amain(args, out, stdin_fd))
        except Exception as e:  # noqa: BLE001 — still end with `closed`
            out.send({"type": "error", "kind": "internal", "message": f"{type(e).__name__}: {e}"})
            reason, code = "error", EXIT_ERROR
        out.send({"type": "closed", "reason": reason})
    finally:
        out.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
