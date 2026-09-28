"""A scripted stand-in for sdk/scraper_login.py (protocol mode) in coordinator tests.

Speaks the helper's JSON-lines protocol: events on stdout, commands on stdin.
Every stdin line is appended to --record (so tests can see what reached the
helper) and echoed to stderr (so tests can prove the manager never logs the
helper's stderr).

Scenarios:
  interactive     ready, status, a frame and pages; then waits for commands.
                  check -> status checking, then status waiting_for_user
                  (or verified with --check-verifies); cancel -> closed(cancelled),
                  exit 2; stdin EOF -> closed(stdin_closed), exit 2.
  verify          ready, status, verified, closed(verified), exit 0.
  browser_closed  ready, status, closed(browser_closed), exit 0.
  hang            ready, then no heartbeats; ignores cancel and SIGTERM.
  engine_missing  error(engine_missing), closed(error), exit 1.
  crash           a few stderr lines, exit 1 with no events.
  straggler       interactive, after starting a sleeper whose argv carries
                  --user-data-dir=<profile> in its own session (--pid-file),
                  the way Playwright leaves Chromium outside the helper's group.
"""
from __future__ import annotations

import argparse
import base64
import json
import signal
import subprocess
import sys
import threading
import time

_out_lock = threading.Lock()


def send(event: dict) -> None:
    line = json.dumps(event, separators=(",", ":")) + "\n"
    with _out_lock:
        sys.stdout.write(line)
        sys.stdout.flush()


def heartbeat_loop(interval: float) -> None:
    while True:
        send({"type": "heartbeat"})
        time.sleep(interval)


def frame(size: int) -> dict:
    pattern = b"\xff\xd8fake-jpeg"
    raw = (pattern * (size // len(pattern) + 1))[:size]
    return {
        "type": "frame",
        "data": base64.b64encode(raw).decode(),
        "metadata": {"deviceWidth": 1280, "deviceHeight": 720, "pageScaleFactor": 1,
                     "offsetTop": 0, "scrollOffsetX": 0, "scrollOffsetY": 0, "timestamp": 1.0},
    }


def ready() -> None:
    send({"type": "ready", "viewport": {"width": 1280, "height": 720}, "headed": False})
    send({"type": "status", "state": "waiting_for_user", "message": "Sign in in the browser."})


def record(path: str | None, line: str) -> None:
    sys.stderr.write(f"fake helper got: {line}\n")
    sys.stderr.flush()
    if path:
        with open(path, "a") as f:
            f.write(line + "\n")


def interactive(args: argparse.Namespace) -> int:
    ready()
    send(frame(args.frame_bytes))
    send({"type": "pages", "pages": [
        {"id": "p1", "url": "https://example.test/login", "title": "Sign in", "active": True},
    ]})
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        record(args.record, line)
        try:
            cmd = json.loads(line).get("cmd")
        except ValueError:
            continue
        if cmd == "cancel":
            send({"type": "closed", "reason": "cancelled"})
            return 2
        if cmd == "check":
            send({"type": "status", "state": "checking", "message": "Checking…"})
            if args.check_verifies:
                send({"type": "verified", "url": "https://example.test/picks"})
                send({"type": "closed", "reason": "verified"})
                return 0
            send({"type": "status", "state": "waiting_for_user",
                  "message": "Not signed in yet — finish signing in, then Check now."})
    send({"type": "closed", "reason": "stdin_closed"})
    return 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="interactive")
    parser.add_argument("--record")
    parser.add_argument("--profile-dir")
    parser.add_argument("--pid-file")
    parser.add_argument("--heartbeat", type=float, default=0.1)
    parser.add_argument("--frame-bytes", type=int, default=64)
    parser.add_argument("--check-verifies", action="store_true")
    args, _ = parser.parse_known_args()

    if args.scenario != "hang":
        threading.Thread(target=heartbeat_loop, args=(args.heartbeat,), daemon=True).start()

    if args.scenario == "interactive":
        return interactive(args)
    if args.scenario == "straggler":
        sleeper = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)",
             f"--user-data-dir={args.profile_dir}"],
            start_new_session=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        with open(args.pid_file, "w") as f:
            f.write(str(sleeper.pid))
        return interactive(args)
    if args.scenario == "verify":
        ready()
        time.sleep(0.05)
        send({"type": "verified", "url": "https://example.test/picks"})
        send({"type": "closed", "reason": "verified"})
        return 0
    if args.scenario == "browser_closed":
        ready()
        time.sleep(0.05)
        send({"type": "closed", "reason": "browser_closed"})
        return 0
    if args.scenario == "engine_missing":
        send({"type": "error", "kind": "engine_missing",
              "message": "patchright is not installed in the scraper's venv; "
                         "run pip install patchright there"})
        send({"type": "closed", "reason": "error"})
        return 1
    if args.scenario == "crash":
        sys.stderr.write("Traceback (most recent call last):\nRuntimeError: boom\n")
        sys.stderr.flush()
        return 1
    if args.scenario == "hang":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        ready()
        for raw in sys.stdin:
            record(args.record, raw.strip())
        while True:
            time.sleep(1)
    raise SystemExit(f"unknown scenario {args.scenario}")


if __name__ == "__main__":
    sys.exit(main())
