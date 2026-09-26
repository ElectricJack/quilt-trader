#!/usr/bin/env python3
"""Deterministic half of the ``quilt-health-watch`` AQ playbook's check task.

The playbook schedules a cheap agent task; the agent runs this script, files a
bugfix task for each new error signature, and closes with the verdict line the
script printed.  Everything that can be decided mechanically is decided here so
the agent's judgement is not on the critical path of a live-money monitor.

    python3 scripts/health_watch.py check
        Probe the coordinator (running the watchdog once if it is down), scan the
        watchdog log and the new bytes of coord.log, read workers/deployments,
        and print a JSON report whose ``verdict_line`` starts with
        ``QUILT-HEALTH: OK|ALERT|KNOWN|CLEARED``.
    python3 scripts/health_watch.py commit --scan SCAN_ID [--filed SIG=TASK ...]
        Record the bugfix tasks the agent filed and the alert state the check
        reported, so the next check neither re-files nor re-alerts.

Read-only except for running the coordinator watchdog once when the health
probe fails.  It never places orders or touches deployments.  Worker JSON
carries an install token, so only name/status/heartbeat ever leave this script.
Stdlib only, so it runs even when the project's own dependencies are broken.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path

HOME = Path.home()
DEFAULTS = {
    "health_url": "http://localhost:8000/api/health",
    "coord_log": HOME / ".quilt/log/coord.log",
    "watchdog_log": HOME / ".quilt/log/watchdog.log",
    "watchdog": HOME / ".quilt/bin/coord-watchdog.sh",
    "state_dir": HOME / ".quilt/run/health-watch",
    "quilt_bin": "quilt",
    "quilt_cwd": HOME / "dev/quilt-trader",
}

#: Re-alert a condition that is still present after this long.
REMIND_SECONDS = 3600
#: A pending signature offered to one check is not offered to another for this long.
CLAIM_SECONDS = 1800
#: Watchdog restarts within the last hour that count as a crash loop.
CRASH_LOOP_RESTARTS = 3
#: Most coord.log bytes one check reads; older unread bytes are skipped and reported.
MAX_SCAN_BYTES = 64 * 1024 * 1024
#: Seconds to wait for health after running the watchdog (covers a cron restart in flight).
RECOVERY_WAIT_SECONDS = 90
EXCERPT_CHARS = 2000
KEEP_SCANS = 20
#: New signatures offered per check; the rest wait for later checks.
MAX_FILINGS = 5
PROJECT_ID = "quilt-trader"

RECORD_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) (\S+) (DEBUG|INFO|WARNING|ERROR|CRITICAL) (.*)$"
)
EXCEPTION_RE = re.compile(r"^([A-Za-z_][\w.]*(Error|Exception|Exit|Interrupt|Fault))(:.*)?$")
HEX_RE = re.compile(r"\b[0-9a-f]{8,}(-[0-9a-f]{4,})*\b", re.IGNORECASE)
NUM_RE = re.compile(r"\d+(\.\d+)?")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def now_utc() -> datetime:
    return datetime.now(UTC)


def normalize(text: str, limit: int) -> str:
    """Collapse ids and numbers so recurrences of one error share a signature."""
    text = HEX_RE.sub("<id>", text)
    text = NUM_RE.sub("N", text)
    return " ".join(text.split())[:limit]


# -- state -----------------------------------------------------------------


def load_state(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None


def save_state(path: Path, state: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    os.replace(tmp, path)


@contextlib.contextmanager
def locked(state_dir: Path):
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(state_dir / "lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


# -- probes ----------------------------------------------------------------


def coordinator_healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return json.loads(response.read().decode()).get("status") == "ok"
    except Exception:  # noqa: BLE001 - any failure is "not healthy"
        return False


def recover_coordinator(args) -> dict:
    """Run the watchdog once, then wait for health (a cron run may hold its lock)."""
    try:
        exit_code = subprocess.run(
            [str(args.watchdog)], capture_output=True, timeout=150, check=False
        ).returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        exit_code = f"{type(exc).__name__}"
    deadline = time.monotonic() + args.recovery_wait
    healthy = coordinator_healthy(args.health_url)
    while not healthy and time.monotonic() < deadline:
        time.sleep(5)
        healthy = coordinator_healthy(args.health_url)
    return {"healthy": healthy, "watchdog_attempted": True, "watchdog_exit": exit_code}


def quilt_json(args, *command: str):
    completed = subprocess.run(
        [args.quilt_bin, "--json", *command],
        capture_output=True, text=True, timeout=30, cwd=args.quilt_cwd, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip()[-300:] or f"exit {completed.returncode}")
    return json.loads(completed.stdout)


def read_fleet(args) -> tuple[list, list, str | None]:
    try:
        workers = [
            {"name": w.get("name"), "status": w.get("status"),
             "last_heartbeat": w.get("last_heartbeat")}
            for w in quilt_json(args, "worker", "list")
        ]
        deployments = [
            {"id": str(d.get("id", ""))[:8], "algorithm": d.get("algorithm_name"),
             "account": d.get("account_name"), "worker": d.get("worker_name"),
             "status": d.get("status")}
            for d in quilt_json(args, "deploy", "list")
        ]
    except Exception as exc:  # noqa: BLE001 - reported as a condition
        return [], [], f"{type(exc).__name__}: {exc}"[:300]
    return workers, deployments, None


# -- logs ------------------------------------------------------------------


def read_new_bytes(path: Path, offset: int) -> tuple[str, int, int, int]:
    """Return (text, new_offset, scanned, skipped) for bytes past *offset*."""
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return "", 0, 0, 0
    if size < offset:  # rotated or truncated
        offset = 0
    skipped = max(0, size - offset - MAX_SCAN_BYTES)
    start = offset + skipped
    with open(path, "rb") as handle:
        handle.seek(start)
        data = handle.read(size - start)
    # Stop at the last complete line so a half-written record is read next time.
    end = data.rfind(b"\n") + 1
    text = CONTROL_RE.sub("", data[:end].decode("utf-8", errors="replace"))
    return text, start + end, end, skipped


def watchdog_summary(path: Path, offset: int, now: datetime) -> tuple[dict, int]:
    text, new_offset, _, _ = read_new_bytes(path, offset)
    since = sum("restarting coordinator" in line for line in text.splitlines())
    failed = sum("still unhealthy after restart" in line for line in text.splitlines())
    last_hour = 0
    try:
        for line in path.read_text(errors="replace").splitlines()[-500:]:
            if "restarting coordinator" not in line:
                continue
            with contextlib.suppress(ValueError, IndexError):
                stamp = datetime.fromisoformat(line.split(" ", 1)[0])
                if (now - stamp).total_seconds() <= 3600:
                    last_hour += 1
    except FileNotFoundError:
        pass
    return (
        {"restarts_since_last": since, "restarts_last_hour": last_hour,
         "failed_restarts_since_last": failed},
        new_offset,
    )


def error_blocks(text: str) -> list[tuple[str, str]]:
    """(signature, excerpt) for each ERROR/CRITICAL record and bare traceback."""
    lines = text.splitlines()
    found: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        match = RECORD_RE.match(lines[i])
        is_error = match is not None and match.group(3) in ("ERROR", "CRITICAL")
        bare = match is None and lines[i].startswith("Traceback (most recent call last)")
        if not (is_error or bare):
            i += 1
            continue
        j = i + 1
        while j < len(lines) and not RECORD_RE.match(lines[j]) and j - i < 60:
            j += 1
        block = lines[i:j]
        exception = next(
            (line for line in reversed(block) if EXCEPTION_RE.match(line.strip())), ""
        )
        if is_error:
            head = f"{match.group(2)} {match.group(3)} {normalize(match.group(4), 160)}"
        else:
            head = "traceback"
            if not exception:  # a warning's stack, not an exception
                i = j
                continue
        signature = head + (f" | {normalize(exception.strip(), 120)}" if exception else "")
        found.append((signature, "\n".join(block)[:EXCERPT_CHARS]))
        i = j
    return found


# -- verdict ---------------------------------------------------------------


def conditions_for(coordinator, restarts, workers, deployments, fleet_error) -> list[str]:
    current = []
    if not coordinator["healthy"]:
        current.append("coordinator_down")
    if restarts["restarts_last_hour"] >= CRASH_LOOP_RESTARTS:
        current.append("crash_loop")
    if coordinator["healthy"]:
        if fleet_error:
            current.append("fleet_unreadable")
        current += [f"worker_offline:{w['name']}" for w in workers if w["status"] != "online"]
        current += [
            f"deployment_not_running:{d['id']}" for d in deployments if d["status"] != "running"
        ]
    return current


def decide(current: list[str], alerts: dict, now_ts: float) -> tuple[str, dict, dict]:
    new = [c for c in current if c not in alerts]
    remind = [c for c in current if c in alerts and now_ts - alerts[c] >= REMIND_SECONDS]
    cleared = sorted(set(alerts) - set(current))
    if new or remind:
        verdict = "ALERT"
    elif current:
        verdict = "KNOWN"
    elif cleared:
        verdict = "CLEARED"
    else:
        verdict = "OK"
    after = {c: (now_ts if c in new or c in remind else alerts[c]) for c in current}
    return verdict, after, {"current": current, "new": new, "reminders": remind, "cleared": cleared}


def verdict_line(verdict, coordinator, restarts, workers, deployments, fleet_error,
                 conditions, to_file) -> str:
    parts = []
    if coordinator["healthy"]:
        parts.append("coordinator restarted by this check, now ok"
                     if coordinator["watchdog_attempted"] else "coordinator ok")
    else:
        parts.append("coordinator DOWN after one watchdog restart attempt")
    if restarts["restarts_since_last"]:
        parts.append(f"watchdog restarted it {restarts['restarts_since_last']}x since last check")
    if "crash_loop" in conditions["current"]:
        parts.append(f"CRASH LOOP: {restarts['restarts_last_hour']} restarts in the last hour")
    if fleet_error:
        parts.append(f"could not read workers/deployments ({fleet_error[:80]})")
    elif coordinator["healthy"]:
        parts += [
            f"{w['name']} {'online' if w['status'] == 'online' else w['status'].upper()}"
            + ("" if w["status"] == "online" else f" (last heartbeat {w['last_heartbeat']})")
            for w in workers
        ]
        running = sum(d["status"] == "running" for d in deployments)
        parts.append(f"{running}/{len(deployments)} deployments running")
        parts += [
            f"deployment {d['id']} {d['algorithm']} on {d['account']} is {d['status']}"
            for d in deployments if d["status"] != "running"
        ]
    parts.append(f"{len(to_file)} new error signature(s)" if to_file else "no new error signatures")
    if conditions["cleared"]:
        parts.append("cleared: " + ", ".join(conditions["cleared"]))
    return f"QUILT-HEALTH: {verdict} - " + "; ".join(parts)


def write_filing(state_dir: Path, sig_id: str, entry: dict, checked_at: str) -> str:
    """Write the bugfix task's title/description to files; return the command.

    Excerpts are tracebacks full of quotes, so the agent passes them through
    ``$(cat ...)`` instead of quoting them itself.
    """
    filings = state_dir / "filings"
    filings.mkdir(parents=True, exist_ok=True)
    title = filings / f"{sig_id}.title"
    body = filings / f"{sig_id}.md"
    title.write_text(f"Quilt error: {entry['signature'][:90]}")
    body.write_text(
        f"Filed by the quilt-health-watch check: a new error signature ({sig_id}) appeared in "
        f"~/.quilt/log/coord.log.\n\nSignature: {entry['signature']}\n\n"
        f"Seen {entry['count']} time(s); first seen by the check at {entry['first_seen']} "
        f"(this check: {checked_at}).\n\nExcerpt:\n\n```text\n{entry['excerpt']}\n```\n"
    )
    return (
        f'aq task create --project {PROJECT_ID} --root --title "$(cat {title})" '
        f'--description "$(cat {body})" '
        f'--reason "quilt-health-watch found new error signature {sig_id} in coord.log"'
    )


# -- commands --------------------------------------------------------------


def cmd_check(args) -> dict:
    state_path = args.state_dir / "state.json"
    coordinator = {"healthy": coordinator_healthy(args.health_url),
                   "watchdog_attempted": False, "watchdog_exit": None}
    if not coordinator["healthy"] and not args.no_repair:
        coordinator = recover_coordinator(args)
    workers, deployments, fleet_error = (
        read_fleet(args) if coordinator["healthy"] else ([], [], None)
    )
    now = now_utc()
    now_ts = now.timestamp()
    scan_id = uuid.uuid4().hex[:12]
    with locked(args.state_dir):
        state = load_state(state_path)
        notes = []
        if state is None:
            size = args.coord_log.stat().st_size if args.coord_log.exists() else 0
            state = {"coord_log_offset": size, "watchdog_log_offset": 0, "alerts": {},
                     "known": {}, "pending": {}, "scans": {}}
            notes.append("first run: coord.log scanning starts at its current end")
        restarts, state["watchdog_log_offset"] = watchdog_summary(
            args.watchdog_log, state["watchdog_log_offset"], now
        )
        text, state["coord_log_offset"], scanned, skipped = read_new_bytes(
            args.coord_log, state["coord_log_offset"]
        )
        if skipped:
            notes.append(f"skipped {skipped} unread coord.log bytes (scan cap)")
        known_counts: dict[str, int] = {}
        for signature, excerpt in error_blocks(text):
            sig_id = hashlib.sha1(signature.encode()).hexdigest()[:12]
            if sig_id in state["known"]:
                known_counts[sig_id] = known_counts.get(sig_id, 0) + 1
                continue
            entry = state["pending"].setdefault(
                sig_id, {"signature": signature, "excerpt": excerpt, "count": 0,
                         "first_seen": now.isoformat(), "claimed_scan": None, "claimed_at": 0}
            )
            entry["count"] += 1
        available = [
            (sig_id, entry) for sig_id, entry in sorted(state["pending"].items())
            if not entry["claimed_scan"] or now_ts - entry["claimed_at"] >= CLAIM_SECONDS
        ]
        if len(available) > MAX_FILINGS:
            notes.append(f"{len(available) - MAX_FILINGS} more new signature(s) wait for "
                         "the next check")
        to_file = []
        for sig_id, entry in available[:MAX_FILINGS]:
            entry["claimed_scan"], entry["claimed_at"] = scan_id, now_ts
            to_file.append({"id": sig_id, "signature": entry["signature"],
                            "count": entry["count"], "excerpt": entry["excerpt"],
                            "file_command": write_filing(args.state_dir, sig_id, entry,
                                                         now.isoformat())})
        current = conditions_for(coordinator, restarts, workers, deployments, fleet_error)
        verdict, alerts_after, conditions = decide(current, state["alerts"], now_ts)
        state["scans"][scan_id] = {"at": now_ts, "alerts_after": alerts_after}
        for old in sorted(state["scans"], key=lambda s: state["scans"][s]["at"])[:-KEEP_SCANS]:
            del state["scans"][old]
        save_state(state_path, state)
    return {
        "scan_id": scan_id,
        "checked_at": now.isoformat(),
        "verdict": verdict,
        "verdict_line": verdict_line(verdict, coordinator, restarts, workers, deployments,
                                     fleet_error, conditions, to_file),
        "coordinator": coordinator,
        "watchdog_log": restarts,
        "workers": workers,
        "deployments": deployments,
        "fleet_error": fleet_error,
        "conditions": conditions,
        "errors": {
            "scanned_bytes": scanned,
            "skipped_bytes": skipped,
            "to_file": to_file,
            "known": [{"id": k, "signature": state["known"][k]["signature"], "count": n,
                       "task_id": state["known"][k]["task_id"]}
                      for k, n in sorted(known_counts.items())],
        },
        "notes": notes,
    }


def cmd_commit(args) -> dict:
    state_path = args.state_dir / "state.json"
    with locked(args.state_dir):
        state = load_state(state_path)
        if state is None or args.scan not in state["scans"]:
            raise SystemExit(f"unknown scan id {args.scan!r}; run `check` first")
        recorded = []
        for pair in args.filed:
            sig_id, _, task_id = pair.partition("=")
            entry = state["pending"].pop(sig_id, None)
            if entry is None or not task_id:
                raise SystemExit(f"--filed {pair!r}: expected SIG_ID=TASK_ID for a pending signature")
            state["known"][sig_id] = {"signature": entry["signature"], "task_id": task_id,
                                      "first_seen": entry["first_seen"]}
            recorded.append(sig_id)
        for entry in state["pending"].values():  # release unfiled claims for the next check
            if entry["claimed_scan"] == args.scan:
                entry["claimed_scan"], entry["claimed_at"] = None, 0
        state["alerts"] = state["scans"].pop(args.scan)["alerts_after"]
        save_state(state_path, state)
    return {"committed": args.scan, "filed": recorded, "alerts": sorted(state["alerts"])}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--state-dir", type=Path, default=DEFAULTS["state_dir"])
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="probe, scan and print the JSON report")
    check.add_argument("--health-url", default=DEFAULTS["health_url"])
    check.add_argument("--coord-log", type=Path, default=DEFAULTS["coord_log"])
    check.add_argument("--watchdog-log", type=Path, default=DEFAULTS["watchdog_log"])
    check.add_argument("--watchdog", type=Path, default=DEFAULTS["watchdog"])
    check.add_argument("--quilt-bin", default=DEFAULTS["quilt_bin"])
    check.add_argument("--quilt-cwd", type=Path, default=DEFAULTS["quilt_cwd"])
    check.add_argument("--recovery-wait", type=float, default=RECOVERY_WAIT_SECONDS)
    check.add_argument("--no-repair", action="store_true", help="never run the watchdog")
    commit = sub.add_parser("commit", help="record filed tasks and the reported alert state")
    commit.add_argument("--scan", required=True)
    commit.add_argument("--filed", action="append", default=[], metavar="SIG_ID=TASK_ID")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    result = cmd_check(args) if args.command == "check" else cmd_commit(args)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
