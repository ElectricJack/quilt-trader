"""The scraper `auth:` contract: manifest block, profile directory, profile lock.

A scraper that reads a site through a persistent, signed-in browser profile
declares it in quilt.yaml:

    auth:
      kind: browser_profile
      engine: patchright              # module providing <engine>.async_api
      profile_dir_param: profile_dir  # config parameter holding the user-data-dir
      login_url: https://example.com/login
      verify:
        url: https://example.com/account
        selector: "#account-name"     # or a list; any one visible counts
        timeout_s: 30
      session_timeout_s: 1200

One parser (`parse_auth`) enforces the rules for `quilt validate`, the
coordinator and the scrape runner alike. `profile_lock` makes sure only one
process (a scrape, a dashboard login session, a terminal login) drives the
profile at a time: two Chromiums on one user-data-dir kill each other with an
opaque "Target page, context or browser has been closed".

This module runs inside scraper package venvs, so it uses the standard
library only. POSIX only (fcntl), like the rest of the coordinator.
"""
from __future__ import annotations

import errno
import fcntl
import json
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator, Optional
from urllib.parse import urlsplit

AUTH_KINDS = ("browser_profile",)
DEFAULT_ENGINE = "playwright"
DEFAULT_VERIFY_TIMEOUT_S = 30
DEFAULT_SESSION_TIMEOUT_S = 1200
MIN_SESSION_TIMEOUT_S = 60
MAX_SESSION_TIMEOUT_S = 3600

LOCK_FILENAME = ".quilt-profile.lock"  # inside the profile dir

_ENGINE_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_AUTH_KEYS = frozenset({
    "kind", "engine", "profile_dir_param", "login_url", "verify", "session_timeout_s",
})
_VERIFY_KEYS = frozenset({"url", "selector", "timeout_s"})
_STRING_PARAM_TYPES = frozenset({"string", "str"})

# How long profile_lock keeps retrying a contended lock before giving up. It
# covers read_profile_holder's momentary probe, not a real holder.
_LOCK_GRACE_S = 0.2
_LOCK_RETRY_S = 0.05

_ROLE_LABELS = {
    "scrape": "scrape",
    "login": "login session",
    "login-local": "terminal login session",
}


class AuthConfigError(ValueError):
    """The manifest's `auth:` block breaks a contract rule."""


@dataclass(frozen=True)
class ScraperAuthVerify:
    """How a login session recognises a signed-in page."""

    url: Optional[str] = None
    selectors: tuple[str, ...] = ()
    timeout_s: int = DEFAULT_VERIFY_TIMEOUT_S


@dataclass(frozen=True)
class ScraperAuth:
    """A parsed `auth:` block with its defaults applied."""

    kind: str
    profile_dir_param: str
    login_url: str
    verify: ScraperAuthVerify
    engine: str = DEFAULT_ENGINE
    session_timeout_s: int = DEFAULT_SESSION_TIMEOUT_S


def _is_http_url(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _config_parameters(manifest: dict) -> list:
    config = manifest.get("config") or {}
    if not isinstance(config, dict):
        return []
    params = config.get("parameters") or []
    return params if isinstance(params, list) else []


def _find_parameter(params: list, name: str) -> Optional[dict]:
    for param in params:
        if isinstance(param, dict) and param.get("name") == name:
            return param
    return None


def _parse_verify(raw: Any) -> ScraperAuthVerify:
    if not isinstance(raw, dict):
        raise AuthConfigError("auth.verify must be a mapping with a url and/or a selector")
    unknown = sorted(set(raw) - _VERIFY_KEYS)
    if unknown:
        raise AuthConfigError(
            f"auth.verify has unknown keys {unknown}; allowed: {sorted(_VERIFY_KEYS)}"
        )
    url = raw.get("url")
    selector = raw.get("selector")
    if url is None and selector is None:
        raise AuthConfigError("auth.verify needs at least one of url and selector")

    if url is not None and not _is_http_url(url):
        raise AuthConfigError(f"auth.verify.url must be an absolute http(s) URL, got {url!r}")

    selectors: tuple[str, ...] = ()
    if selector is not None:
        items = [selector] if isinstance(selector, str) else selector
        if (
            not isinstance(items, list)
            or not items
            or not all(isinstance(s, str) and s.strip() for s in items)
        ):
            raise AuthConfigError(
                "auth.verify.selector must be a non-empty string or a non-empty "
                f"list of non-empty strings, got {selector!r}"
            )
        selectors = tuple(items)

    timeout_s = raw.get("timeout_s", DEFAULT_VERIFY_TIMEOUT_S)
    if not _is_int(timeout_s) or timeout_s <= 0:
        raise AuthConfigError(
            f"auth.verify.timeout_s must be a positive integer, got {timeout_s!r}"
        )
    return ScraperAuthVerify(url=url, selectors=selectors, timeout_s=timeout_s)


def parse_auth(manifest: dict) -> Optional[ScraperAuth]:
    """Return the manifest's `auth:` block as a ScraperAuth, or None if absent.

    Raises AuthConfigError when the block breaks a rule. Every consumer
    (manifest validation, the coordinator's registry, the scrape runner) calls
    this one function so they agree on what a valid block is.
    """
    if not isinstance(manifest, dict) or "auth" not in manifest:
        return None
    raw = manifest["auth"]
    if manifest.get("type") != "scraper":
        raise AuthConfigError(
            f"auth is only allowed when type is 'scraper', got type {manifest.get('type')!r}"
        )
    if not isinstance(raw, dict):
        raise AuthConfigError(f"auth must be a mapping, got {type(raw).__name__}")

    unknown = sorted(set(raw) - _AUTH_KEYS)
    if unknown:
        raise AuthConfigError(f"auth has unknown keys {unknown}; allowed: {sorted(_AUTH_KEYS)}")

    kind = raw.get("kind")
    if kind not in AUTH_KINDS:
        raise AuthConfigError(f"auth.kind must be one of {list(AUTH_KINDS)}, got {kind!r}")

    engine = raw.get("engine", DEFAULT_ENGINE)
    if not isinstance(engine, str) or not _ENGINE_RE.match(engine):
        raise AuthConfigError(
            f"auth.engine must match {_ENGINE_RE.pattern!r} (a module name), got {engine!r}"
        )

    param_name = raw.get("profile_dir_param")
    if not isinstance(param_name, str) or not param_name:
        raise AuthConfigError(
            "auth.profile_dir_param must name the config parameter holding the "
            f"browser profile directory, got {param_name!r}"
        )
    param = _find_parameter(_config_parameters(manifest), param_name)
    if param is None:
        raise AuthConfigError(
            f"auth.profile_dir_param {param_name!r} is not a declared config.parameters entry"
        )
    if param.get("type") not in _STRING_PARAM_TYPES:
        raise AuthConfigError(
            f"auth.profile_dir_param {param_name!r} must be a config parameter of type "
            f"string, got type {param.get('type')!r}"
        )

    login_url = raw.get("login_url")
    if not _is_http_url(login_url):
        raise AuthConfigError(f"auth.login_url must be an absolute http(s) URL, got {login_url!r}")

    if "verify" not in raw:
        raise AuthConfigError("auth.verify is required (a url and/or a selector)")
    verify = _parse_verify(raw["verify"])

    session_timeout_s = raw.get("session_timeout_s", DEFAULT_SESSION_TIMEOUT_S)
    if (
        not _is_int(session_timeout_s)
        or not MIN_SESSION_TIMEOUT_S <= session_timeout_s <= MAX_SESSION_TIMEOUT_S
    ):
        raise AuthConfigError(
            f"auth.session_timeout_s must be an integer from {MIN_SESSION_TIMEOUT_S} to "
            f"{MAX_SESSION_TIMEOUT_S}, got {session_timeout_s!r}"
        )

    return ScraperAuth(
        kind=kind,
        engine=engine,
        profile_dir_param=param_name,
        login_url=login_url,
        verify=verify,
        session_timeout_s=session_timeout_s,
    )


def resolve_profile_dir(
    manifest: Any,
    overrides: Optional[dict] = None,
    *,
    base_dir: Optional[str] = None,
) -> str:
    """Absolute path of the scraper's browser profile directory.

    `manifest` is a manifest dict or a QuiltManifest. The value is
    overrides[profile_dir_param] when present, otherwise that parameter's
    manifest default, with `~` expanded. A relative path is taken relative to
    `base_dir` (the package directory, where scrapes run) or, without one, the
    current directory. The coordinator, the scrape runner and the login helper
    all call this with the same inputs, so they agree on the directory.

    Raises AuthConfigError when the manifest has no usable auth block or the
    parameter resolves to nothing.
    """
    if isinstance(manifest, dict):
        auth = parse_auth(manifest)
        params = _config_parameters(manifest)
    else:
        auth = getattr(manifest, "auth", None)
        params = list(getattr(manifest, "config_parameters", None) or [])
    if auth is None:
        raise AuthConfigError("manifest has no auth: block")

    name = auth.profile_dir_param
    if overrides and name in overrides:
        value = overrides[name]
    else:
        value = (_find_parameter(params, name) or {}).get("default")
    if not isinstance(value, str) or not value.strip():
        raise AuthConfigError(
            f"config parameter {name!r} gives no browser profile directory "
            f"(value {value!r}); set it in the scraper's config"
        )
    path = os.path.expanduser(value.strip())
    if not os.path.isabs(path) and base_dir is not None:
        path = os.path.join(base_dir, path)
    return os.path.abspath(path)


class ProfileBusy(Exception):
    """Another process holds the browser profile."""

    def __init__(self, profile_dir: str, holder: Optional[dict] = None) -> None:
        self.profile_dir = profile_dir
        self.holder: Optional[dict] = holder
        super().__init__(f"profile {profile_dir} is {describe_holder(holder)}")


def describe_holder(holder: Optional[dict]) -> str:
    """Human wording for a holder: 'in use by login session, pid 1234, since 14:02 UTC'."""
    if not holder:
        return "in use by another process"
    role = holder.get("role")
    parts = [f"in use by {_ROLE_LABELS.get(role, role or 'another process')}"]
    if holder.get("pid") is not None:
        parts.append(f"pid {holder['pid']}")
    started_at = holder.get("started_at")
    if isinstance(started_at, str):
        try:
            started = datetime.fromisoformat(started_at).astimezone(timezone.utc)
            parts.append(f"since {started:%H:%M} UTC")
        except ValueError:
            parts.append(f"since {started_at}")
    return ", ".join(parts)


def _lock_path(profile_dir: str) -> str:
    return os.path.join(profile_dir, LOCK_FILENAME)


def _try_flock(fd: int, operation: int) -> bool:
    try:
        fcntl.flock(fd, operation | fcntl.LOCK_NB)
    except OSError as e:
        if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
            return False
        raise
    return True


def _read_holder_fd(fd: int) -> Optional[dict]:
    """The holder JSON in an open lock file, or None if it is empty or garbled.

    A new holder rewrites the file right after taking the lock, so a read in
    that instant can see nothing yet; retry briefly before giving up.
    """
    for attempt in range(5):
        try:
            raw = os.pread(fd, 4096, 0)
            holder = json.loads(raw.decode("utf-8")) if raw.strip() else None
        except (OSError, ValueError):
            holder = None
        if isinstance(holder, dict):
            return holder
        if attempt < 4:
            time.sleep(0.02)
    return None


@contextmanager
def profile_lock(profile_dir: str, role: str) -> Iterator[None]:
    """Hold the browser profile exclusively for the duration of the block.

    Takes fcntl.flock on <profile_dir>/.quilt-profile.lock and records
    {pid, role, started_at} in it. Raises ProfileBusy with the current
    holder's record when another process holds it. The kernel drops the lock
    when the descriptor closes, including when the process dies, so a crash
    never leaves a stale lock. The descriptor is non-inheritable (PEP 446), so
    a browser launched inside the block does not keep the lock alive.
    """
    os.makedirs(profile_dir, exist_ok=True)
    fd = os.open(_lock_path(profile_dir), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + _LOCK_GRACE_S
        while not _try_flock(fd, fcntl.LOCK_EX):
            if time.monotonic() >= deadline:
                raise ProfileBusy(profile_dir, _read_holder_fd(fd))
            time.sleep(_LOCK_RETRY_S)

        holder = {
            "pid": os.getpid(),
            "role": role,
            "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        os.ftruncate(fd, 0)
        os.pwrite(fd, json.dumps(holder).encode("utf-8"), 0)
        try:
            yield
        finally:
            # Clear the record before the kernel releases the lock, so a
            # stale holder never outlives its owner in the file.
            try:
                os.ftruncate(fd, 0)
            except OSError:
                pass
    finally:
        os.close(fd)


def read_profile_holder(profile_dir: str) -> Optional[dict]:
    """The current holder's {pid, role, started_at}, or None when the profile is free.

    Probes the lock without blocking and releases it at once. This only gives
    a clear message before spawning something; the lock each process takes
    for itself is the real guard.
    """
    try:
        fd = os.open(_lock_path(profile_dir), os.O_RDONLY)
    except FileNotFoundError:
        return None
    try:
        if _try_flock(fd, fcntl.LOCK_SH):
            fcntl.flock(fd, fcntl.LOCK_UN)
            return None
        # Held, but the record is unreadable: still report it as busy.
        return _read_holder_fd(fd) or {"pid": None, "role": None, "started_at": None}
    finally:
        os.close(fd)
