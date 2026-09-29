"""The scraper auth: contract (review rev-nimble-bridge, sections 5 and 8)."""
from __future__ import annotations

import copy
import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from sdk.scraper_auth import (
    DEFAULT_ENGINE,
    DEFAULT_SESSION_TIMEOUT_S,
    DEFAULT_VERIFY_TIMEOUT_S,
    LOCK_FILENAME,
    AuthConfigError,
    ProfileBusy,
    ScraperAuth,
    ScraperAuthVerify,
    parse_auth,
    profile_lock,
    read_profile_holder,
    resolve_profile_dir,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _manifest(**auth_overrides) -> dict:
    auth = {
        "kind": "browser_profile",
        "engine": "patchright",
        "profile_dir_param": "profile_dir",
        "login_url": "https://example.com/login",
        "verify": {
            "url": "https://example.com/picks",
            "selector": ["table.picks", "#current-picks"],
            "timeout_s": 20,
        },
        "session_timeout_s": 900,
    }
    for key, value in auth_overrides.items():
        if value is _DROP:
            auth.pop(key, None)
        else:
            auth[key] = value
    return {
        "name": "demo-scraper",
        "type": "scraper",
        "schedule": "0 14 * * 1-5",
        "config": {"parameters": [
            {"name": "profile_dir", "type": "string", "default": "~/.cache/demo-profile"},
            {"name": "timeout_ms", "type": "int", "default": 45000},
            {"name": "headless", "type": "bool", "default": True},
        ]},
        "auth": auth,
    }


_DROP = object()


# --- parse_auth ---------------------------------------------------------------

class TestParseAuth:
    def test_absent_block_is_none(self):
        manifest = _manifest()
        del manifest["auth"]
        assert parse_auth(manifest) is None

    def test_full_block(self):
        auth = parse_auth(_manifest())
        assert auth == ScraperAuth(
            kind="browser_profile",
            engine="patchright",
            profile_dir_param="profile_dir",
            login_url="https://example.com/login",
            verify=ScraperAuthVerify(
                url="https://example.com/picks",
                selectors=("table.picks", "#current-picks"),
                timeout_s=20,
            ),
            session_timeout_s=900,
        )

    def test_defaults_applied(self):
        auth = parse_auth(_manifest(
            engine=_DROP, session_timeout_s=_DROP,
            verify={"selector": "table.picks"},
        ))
        assert auth.engine == DEFAULT_ENGINE == "playwright"
        assert auth.session_timeout_s == DEFAULT_SESSION_TIMEOUT_S == 1200
        assert auth.verify.url is None
        assert auth.verify.selectors == ("table.picks",)
        assert auth.verify.timeout_s == DEFAULT_VERIFY_TIMEOUT_S == 30

    def test_frozen(self):
        auth = parse_auth(_manifest())
        with pytest.raises(Exception):
            auth.engine = "playwright"  # type: ignore[misc]

    def test_verify_url_only(self):
        auth = parse_auth(_manifest(verify={"url": "https://example.com/*"}))
        assert auth.verify.url == "https://example.com/*"
        assert auth.verify.selectors == ()

    def test_session_timeout_bounds_inclusive(self):
        assert parse_auth(_manifest(session_timeout_s=60)).session_timeout_s == 60
        assert parse_auth(_manifest(session_timeout_s=3600)).session_timeout_s == 3600

    def test_str_parameter_type_accepted(self):
        manifest = _manifest()
        manifest["config"]["parameters"][0]["type"] = "str"
        assert parse_auth(manifest).profile_dir_param == "profile_dir"

    @pytest.mark.parametrize("mutate, match", [
        (lambda m: m.update(type="algorithm"), "only allowed when type is 'scraper'"),
        (lambda m: m.update(auth=None), "must be a mapping"),
        (lambda m: m.update(auth=["kind", "browser_profile"]), "must be a mapping"),
        (lambda m: m["auth"].update(kind="api_key"), "auth.kind"),
        (lambda m: m["auth"].pop("kind"), "auth.kind"),
        (lambda m: m["auth"].update(profile_dir_param="nope"), "not a declared config.parameters"),
        (lambda m: m["auth"].update(profile_dir_param="timeout_ms"), "of type string"),
        (lambda m: m["auth"].pop("profile_dir_param"), "profile_dir_param"),
        (lambda m: m.pop("config"), "not a declared config.parameters"),
        (lambda m: m["auth"].update(login_url="/login"), "login_url"),
        (lambda m: m["auth"].update(login_url="ftp://example.com/x"), "login_url"),
        (lambda m: m["auth"].pop("login_url"), "login_url"),
        (lambda m: m["auth"].pop("verify"), "auth.verify is required"),
        (lambda m: m["auth"].update(verify="table"), "auth.verify must be a mapping"),
        (lambda m: m["auth"].update(verify={"timeout_s": 5}), "at least one of url and selector"),
        (lambda m: m["auth"].update(verify={"url": "example.com/x"}), "verify.url"),
        (lambda m: m["auth"].update(verify={"selector": []}), "verify.selector"),
        (lambda m: m["auth"].update(verify={"selector": ["ok", 3]}), "verify.selector"),
        (lambda m: m["auth"].update(verify={"selector": ""}), "verify.selector"),
        (lambda m: m["auth"].update(verify={"selector": "t", "timeout_s": 0}), "timeout_s"),
        (lambda m: m["auth"].update(verify={"selector": "t", "timeout_s": True}), "timeout_s"),
        (lambda m: m["auth"].update(verify={"selector": "t", "selectors": ["x"]}), "unknown keys"),
        (lambda m: m["auth"].update(engine="Patch-Right"), "auth.engine"),
        (lambda m: m["auth"].update(engine="os.system"), "auth.engine"),
        (lambda m: m["auth"].update(session_timeout_s=59), "session_timeout_s"),
        (lambda m: m["auth"].update(session_timeout_s=3601), "session_timeout_s"),
        (lambda m: m["auth"].update(session_timeout_s="1200"), "session_timeout_s"),
        (lambda m: m["auth"].update(login_urll="https://x.com"), "unknown keys"),
    ])
    def test_rules(self, mutate, match):
        manifest = copy.deepcopy(_manifest())
        mutate(manifest)
        with pytest.raises(AuthConfigError, match=match):
            parse_auth(manifest)

    def test_auth_config_error_is_value_error(self):
        assert issubclass(AuthConfigError, ValueError)


# --- resolve_profile_dir ------------------------------------------------------

class TestResolveProfileDir:
    def test_manifest_default_with_tilde(self):
        expected = os.path.join(os.path.expanduser("~"), ".cache", "demo-profile")
        assert resolve_profile_dir(_manifest(), {}) == expected

    def test_override_wins(self, tmp_path):
        target = str(tmp_path / "profile")
        assert resolve_profile_dir(_manifest(), {"profile_dir": target}) == target

    def test_override_tilde_expanded(self):
        path = resolve_profile_dir(_manifest(), {"profile_dir": "~/p"})
        assert path == os.path.join(os.path.expanduser("~"), "p")

    def test_relative_path_joins_base_dir(self, tmp_path):
        path = resolve_profile_dir(_manifest(), {"profile_dir": "state/profile"},
                                   base_dir=str(tmp_path))
        assert path == str(tmp_path / "state" / "profile")

    def test_accepts_quilt_manifest(self, tmp_path):
        import yaml
        from sdk.manifest import QuiltManifest

        manifest = QuiltManifest.from_string(yaml.safe_dump(_manifest()))
        target = str(tmp_path / "p")
        assert resolve_profile_dir(manifest, {"profile_dir": target}) == target
        assert resolve_profile_dir(manifest, None) == resolve_profile_dir(_manifest(), None)

    def test_no_auth_block_raises(self):
        manifest = _manifest()
        del manifest["auth"]
        with pytest.raises(AuthConfigError, match="no auth"):
            resolve_profile_dir(manifest, {})

    def test_empty_value_raises(self):
        with pytest.raises(AuthConfigError, match="no browser profile directory"):
            resolve_profile_dir(_manifest(), {"profile_dir": ""})


# --- profile_lock / read_profile_holder ---------------------------------------

_HOLDER_SCRIPT = textwrap.dedent("""
    import sys, time
    from sdk.scraper_auth import profile_lock
    with profile_lock(sys.argv[1], sys.argv[2]):
        print("locked", flush=True)
        time.sleep(60)
""")


def _spawn_holder(profile_dir: str, role: str) -> subprocess.Popen:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    proc = subprocess.Popen(
        [sys.executable, "-c", _HOLDER_SCRIPT, profile_dir, role],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=str(REPO_ROOT),
    )
    line = proc.stdout.readline()
    if line.strip() != "locked":
        proc.kill()
        _, err = proc.communicate(timeout=10)
        pytest.fail(f"holder did not take the lock: {line!r} {err}")
    return proc


class TestProfileLock:
    def test_holder_record_written_and_cleared(self, tmp_path):
        profile = str(tmp_path / "profile")
        lock_file = os.path.join(profile, LOCK_FILENAME)
        with profile_lock(profile, "scrape"):
            holder = json.loads(Path(lock_file).read_text())
            assert holder["pid"] == os.getpid()
            assert holder["role"] == "scrape"
            assert holder["started_at"].endswith("+00:00")
            assert (os.stat(lock_file).st_mode & 0o777) == 0o600
            assert read_profile_holder(profile) == holder
        assert read_profile_holder(profile) is None
        assert Path(lock_file).read_text() == ""

    def test_creates_missing_profile_dir(self, tmp_path):
        profile = tmp_path / "a" / "b"
        with profile_lock(str(profile), "login"):
            assert profile.is_dir()

    def test_free_profile_reads_none_without_creating_anything(self, tmp_path):
        profile = tmp_path / "never-used"
        assert read_profile_holder(str(profile)) is None
        assert not profile.exists()

    def test_released_after_exception(self, tmp_path):
        profile = str(tmp_path / "profile")
        with pytest.raises(RuntimeError):
            with profile_lock(profile, "scrape"):
                raise RuntimeError("boom")
        assert read_profile_holder(profile) is None
        with profile_lock(profile, "login"):
            pass

    def test_second_process_gets_profile_busy_until_holder_dies(self, tmp_path):
        profile = str(tmp_path / "profile")
        holder_proc = _spawn_holder(profile, "login")
        try:
            # This test process is the second contender.
            with pytest.raises(ProfileBusy) as excinfo:
                with profile_lock(profile, "scrape"):
                    pytest.fail("took a lock another process holds")
            busy = excinfo.value
            assert busy.holder["pid"] == holder_proc.pid
            assert busy.holder["role"] == "login"
            assert busy.holder["started_at"]
            assert f"in use by login session, pid {holder_proc.pid}, since " in str(busy)
            assert str(busy).endswith(" UTC")

            observed = read_profile_holder(profile)
            assert observed["pid"] == holder_proc.pid
            assert observed["role"] == "login"
        finally:
            # SIGKILL: no finally blocks run in the holder, only the kernel
            # closing its descriptors releases the lock.
            holder_proc.send_signal(signal.SIGKILL)
            holder_proc.communicate(timeout=10)

        assert read_profile_holder(profile) is None
        with profile_lock(profile, "scrape"):
            assert read_profile_holder(profile)["pid"] == os.getpid()

    def test_third_process_also_sees_busy(self, tmp_path):
        """Contention between two child processes, not just the test process."""
        profile = str(tmp_path / "profile")
        holder_proc = _spawn_holder(profile, "login-local")
        try:
            env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
            probe = subprocess.run(
                [sys.executable, "-c", textwrap.dedent("""
                    import json, sys
                    from sdk.scraper_auth import ProfileBusy, profile_lock
                    try:
                        with profile_lock(sys.argv[1], "scrape"):
                            print(json.dumps({"locked": True}))
                    except ProfileBusy as e:
                        print(json.dumps({"busy": e.holder, "message": str(e)}))
                """), profile],
                capture_output=True, text=True, env=env, cwd=str(REPO_ROOT), timeout=60,
            )
            assert probe.returncode == 0, probe.stderr
            out = json.loads(probe.stdout)
            assert out["busy"]["pid"] == holder_proc.pid
            assert out["busy"]["role"] == "login-local"
            assert "terminal login session" in out["message"]
        finally:
            holder_proc.kill()
            holder_proc.communicate(timeout=10)

    def test_busy_even_within_one_process(self, tmp_path):
        profile = str(tmp_path / "profile")
        with profile_lock(profile, "login"):
            start = time.monotonic()
            with pytest.raises(ProfileBusy) as excinfo:
                with profile_lock(profile, "scrape"):
                    pass
            assert excinfo.value.holder["role"] == "login"
            assert time.monotonic() - start < 2

    def test_unreadable_record_still_reported_busy(self, tmp_path):
        profile = str(tmp_path / "profile")
        with profile_lock(profile, "login"):
            Path(profile, LOCK_FILENAME).write_text("not json")
            holder = read_profile_holder(profile)
            assert holder is not None and holder["pid"] is None
            with pytest.raises(ProfileBusy, match="in use by another process"):
                with profile_lock(profile, "scrape"):
                    pass
