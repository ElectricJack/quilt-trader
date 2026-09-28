"""Child entrypoint for one scrape, run inside the scraper package's venv.

ScraperEngine.run_scraper starts it as

    <venv python> -c "import sys; sys.path.insert(0, PKG); sys.path.append(ROOT); \\
                      from sdk.scraper_runner import main; sys.exit(main())" \\
        --pkg-dir PKG --config-json CFG --out OUT_CSV --result RESULT_JSON

and reads RESULT_JSON afterwards. Every outcome the runner can catch gets a
result file and its own exit code:

    success           {"status": "ok", "rows": N}                          0
    any other error   {"status": "error", "error_type", "message"}         1
                      (plus a traceback on stderr)
    ScraperAuthError  {"status": "auth_required" | "bot_blocked", "message"} 2
    profile busy      {"status": "profile_busy", "holder": {pid, role,
                       started_at}, "message"}                             3

A scraper whose manifest declares `auth: {kind: browser_profile}` runs its
whole on_start -> on_run -> to_csv -> on_stop sequence under the profile
lock (role "scrape"), so it can never share the profile with a login session.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
import traceback
from contextlib import nullcontext
from typing import Optional

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_AUTH = 2
EXIT_PROFILE_BUSY = 3

AUTH_STATUSES = ("auth_required", "bot_blocked")


def _parse_args(argv: Optional[list[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="sdk.scraper_runner")
    parser.add_argument("--pkg-dir", required=True)
    parser.add_argument("--config-json", default="{}")
    parser.add_argument("--out", required=True)
    parser.add_argument("--result", required=True)
    return parser.parse_args(argv)


def write_result(path: str, payload: dict) -> None:
    """Write the result file atomically, so a reader never sees half of it."""
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".scraper-result-", dir=directory)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load_manifest(pkg_dir: str) -> dict:
    import yaml

    with open(os.path.join(pkg_dir, "quilt.yaml")) as f:
        return yaml.safe_load(f) or {}


def _load_scraper(pkg_dir: str, manifest: dict):
    entry = manifest.get("entry_point", "scraper.py")
    class_name = manifest.get("class_name", "Scraper")
    spec = importlib.util.spec_from_file_location("mod", os.path.join(pkg_dir, entry))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return getattr(mod, class_name)()


def _profile_guard(pkg_dir: str, manifest: dict, config: dict):
    """The profile lock for a browser_profile scraper, or a no-op context.

    A broken auth block or an unresolvable profile directory is reported on
    stderr and the scrape runs unlocked, as it did before the contract: the
    coordinator registers such a scraper without auth, and a typo must not
    stop the scrape itself.
    """
    from sdk.scraper_auth import AuthConfigError, parse_auth, profile_lock, resolve_profile_dir

    try:
        auth = parse_auth(manifest)
        if auth is None or auth.kind != "browser_profile":
            return nullcontext()
        profile_dir = resolve_profile_dir(manifest, config, base_dir=pkg_dir)
    except AuthConfigError as e:
        print(f"warning: ignoring auth: block, running without the profile lock: {e}",
              file=sys.stderr)
        return nullcontext()
    return profile_lock(profile_dir, "scrape")


def _scrape(pkg_dir: str, config: dict, out_path: str) -> int:
    manifest = _load_manifest(pkg_dir)
    scraper = _load_scraper(pkg_dir, manifest)
    with _profile_guard(pkg_dir, manifest, config):
        scraper.on_start(config)
        df = scraper.on_run()
        df.to_csv(out_path, index=False)
        scraper.on_stop()
    return len(df)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    from sdk.scraper import ScraperAuthError
    from sdk.scraper_auth import ProfileBusy

    try:
        config = json.loads(args.config_json)
        if not isinstance(config, dict):
            raise ValueError(f"--config-json must be a JSON object, got {type(config).__name__}")
        rows = _scrape(args.pkg_dir, config, args.out)
    except ScraperAuthError as e:
        status = e.kind if e.kind in AUTH_STATUSES else "auth_required"
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        write_result(args.result, {"status": status, "message": str(e)})
        return EXIT_AUTH
    except ProfileBusy as e:
        print(f"ProfileBusy: {e}", file=sys.stderr)
        write_result(args.result, {
            "status": "profile_busy", "holder": e.holder, "message": str(e),
        })
        return EXIT_PROFILE_BUSY
    except Exception as e:  # noqa: BLE001 — every failure becomes a result file
        traceback.print_exc()
        write_result(args.result, {
            "status": "error", "error_type": type(e).__name__, "message": str(e),
        })
        return EXIT_ERROR
    write_result(args.result, {"status": "ok", "rows": rows})
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
