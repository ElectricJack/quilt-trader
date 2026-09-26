"""Unit tests for scripts/health_watch.py, the deterministic half of the
quilt-health-watch playbook's check task."""
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "health_watch.py"
_spec = importlib.util.spec_from_file_location("health_watch", SCRIPT)
hw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hw)

WORKER_ONLINE = [{"id": "ee10e6cf-1", "name": "Trader1", "status": "online",
                  "last_heartbeat": "2026-09-26T16:28:49Z", "install_token": "SECRET-TOKEN"}]
DEPLOYMENTS = [
    {"id": "70d6b269-aaaa", "algorithm_name": "alpha-picks-rebalancer",
     "account_name": "Alpaca Personal", "worker_name": "Trader1", "status": "running"},
    {"id": "4c81384e-bbbb", "algorithm_name": "rates-trend",
     "account_name": "Tradier Personal", "worker_name": "Trader1", "status": "running"},
]
ERROR_BLOCK = (
    "2026-09-26 10:15:00,123 worker.tradier_adapter ERROR trade handler error for order 12345\n"
    "Traceback (most recent call last):\n"
    '  File "/x/adapter.py", line 88, in handle\n'
    "    raise ValueError('bad fill 12345')\n"
    "ValueError: bad fill 12345\n"
)
NOISE = (
    "/home/x/site-packages/quantstats/stats.py:1966: RuntimeWarning: Mean of empty slice.\n"
    "  c_var = returns[returns < var].values.mean()\n"
    "2026-09-26 10:15:01,000 httpx INFO HTTP Request: GET /api 200\n"
)


class Env:
    def __init__(self, tmp_path: Path):
        self.root = tmp_path
        self.health = tmp_path / "health.json"
        self.coord_log = tmp_path / "coord.log"
        self.watchdog_log = tmp_path / "watchdog.log"
        self.state_dir = tmp_path / "state"
        self.fleet = tmp_path / "fleet.json"
        self.watchdog = tmp_path / "watchdog.sh"
        self.quilt = tmp_path / "quilt"
        self.set_health(True)
        self.coord_log.write_text("2026-09-26 09:00:00,000 coordinator.main INFO started\n")
        self.set_fleet(WORKER_ONLINE, DEPLOYMENTS)
        self.quilt.write_text(
            f"#!{sys.executable}\n"
            "import json, sys\n"
            f"fleet = json.load(open({str(self.fleet)!r}))\n"
            "print(json.dumps(fleet[sys.argv[2]]))\n"
        )
        self.quilt.chmod(0o755)
        self.set_watchdog(fixes=True)

    def set_health(self, ok: bool):
        if ok:
            self.health.write_text(json.dumps({"status": "ok"}))
        elif self.health.exists():
            self.health.unlink()

    def set_fleet(self, workers, deployments):
        self.fleet.write_text(json.dumps({"worker": workers, "deploy": deployments}))

    def set_watchdog(self, fixes: bool):
        body = f"echo '{{\"status\": \"ok\"}}' > {self.health}\n" if fixes else "exit 1\n"
        self.watchdog.write_text("#!/bin/bash\n" + f"touch {self.root / 'watchdog-ran'}\n" + body)
        self.watchdog.chmod(0o755)

    def append_log(self, text: str):
        with open(self.coord_log, "a") as handle:
            handle.write(text)

    def check(self):
        args = hw.parse_args([
            "--state-dir", str(self.state_dir), "check",
            "--health-url", self.health.as_uri(),
            "--coord-log", str(self.coord_log),
            "--watchdog-log", str(self.watchdog_log),
            "--watchdog", str(self.watchdog),
            "--quilt-bin", str(self.quilt),
            "--quilt-cwd", str(self.root),
            "--recovery-wait", "0",
        ])
        return hw.cmd_check(args)

    def commit(self, scan_id, *filed):
        argv = ["--state-dir", str(self.state_dir), "commit", "--scan", scan_id]
        for pair in filed:
            argv += ["--filed", pair]
        return hw.cmd_commit(hw.parse_args(argv))


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def test_first_run_is_ok_and_starts_at_end_of_log(env):
    env.append_log(ERROR_BLOCK)  # history before the first check is not reported
    report = env.check()
    assert report["verdict"] == "OK"
    assert report["verdict_line"].startswith("QUILT-HEALTH: OK - coordinator ok")
    assert "Trader1 online" in report["verdict_line"]
    assert "2/2 deployments running" in report["verdict_line"]
    assert report["errors"]["to_file"] == []
    assert "SECRET-TOKEN" not in json.dumps(report)


def test_new_error_signature_is_offered_once_then_known(env):
    env.commit(env.check()["scan_id"])
    env.append_log(NOISE + ERROR_BLOCK + NOISE + ERROR_BLOCK.replace("12345", "67890"))
    report = env.check()
    to_file = report["errors"]["to_file"]
    assert len(to_file) == 1  # numbers normalised; RuntimeWarning noise ignored
    assert to_file[0]["count"] == 2
    assert to_file[0]["signature"].startswith("worker.tradier_adapter ERROR trade handler error")
    assert to_file[0]["signature"].endswith("| ValueError: bad fill N")
    assert "Traceback" in to_file[0]["excerpt"]
    assert report["verdict"] == "OK"  # an error signature is filed, not alerted
    env.commit(report["scan_id"], f"{to_file[0]['id']}=task-1")
    env.append_log(ERROR_BLOCK)
    again = env.check()
    assert again["errors"]["to_file"] == []
    assert again["errors"]["known"][0]["task_id"] == "task-1"


def test_filing_files_carry_the_excerpt_and_the_command_quotes_nothing_itself(env):
    env.commit(env.check()["scan_id"])
    env.append_log(ERROR_BLOCK)
    entry = env.check()["errors"]["to_file"][0]
    filings = env.state_dir / "filings"
    title = (filings / f"{entry['id']}.title").read_text()
    body = (filings / f"{entry['id']}.md").read_text()
    assert title.startswith("Quilt error: worker.tradier_adapter ERROR trade handler error")
    assert "raise ValueError('bad fill 12345')" in body
    assert entry["file_command"] == (
        f'aq task create --project quilt-trader --root --title "$(cat {filings}/{entry["id"]}.title)" '
        f'--description "$(cat {filings}/{entry["id"]}.md)" '
        f'--reason "quilt-health-watch found new error signature {entry["id"]} in coord.log"'
    )


def test_at_most_five_signatures_are_offered_per_check(env):
    env.commit(env.check()["scan_id"])
    env.append_log("".join(
        f"2026-09-26 10:15:00,123 worker.m{n} ERROR failure kind {chr(97 + n)}\n" for n in range(7)
    ))
    first = env.check()
    assert len(first["errors"]["to_file"]) == 5
    assert "2 more new signature(s) wait for the next check" in first["notes"]
    env.commit(first["scan_id"], *(f"{e['id']}=t{n}" for n, e in enumerate(first["errors"]["to_file"])))
    assert len(env.check()["errors"]["to_file"]) == 2


def test_unfiled_signature_is_reoffered_after_commit_but_not_to_a_concurrent_check(env):
    env.commit(env.check()["scan_id"])
    env.append_log(ERROR_BLOCK)
    first = env.check()
    assert len(first["errors"]["to_file"]) == 1
    concurrent = env.check()  # claim still held by the first check
    assert concurrent["errors"]["to_file"] == []
    env.commit(first["scan_id"])  # agent filed nothing: claim released
    assert len(env.check()["errors"]["to_file"]) == 1


def test_down_coordinator_recovered_by_watchdog_is_not_an_alert(env):
    env.set_health(False)
    report = env.check()
    assert (env.root / "watchdog-ran").exists()
    assert report["coordinator"] == {"healthy": True, "watchdog_attempted": True,
                                     "watchdog_exit": 0}
    assert report["verdict"] == "OK"
    assert "coordinator restarted by this check, now ok" in report["verdict_line"]


def test_unrecoverable_coordinator_alerts_then_known_then_cleared(env):
    env.set_health(False)
    env.set_watchdog(fixes=False)
    report = env.check()
    assert report["verdict"] == "ALERT"
    assert report["conditions"]["new"] == ["coordinator_down"]
    assert "coordinator DOWN" in report["verdict_line"]
    env.commit(report["scan_id"])
    known = env.check()
    assert known["verdict"] == "KNOWN"  # already alerted within the hour
    env.commit(known["scan_id"])
    env.set_health(True)
    cleared = env.check()
    assert cleared["verdict"] == "CLEARED"
    assert "cleared: coordinator_down" in cleared["verdict_line"]
    env.commit(cleared["scan_id"])
    assert env.check()["verdict"] == "OK"


def test_persisting_condition_is_reminded_after_an_hour(env):
    state_path = env.state_dir / "state.json"
    env.set_fleet([dict(WORKER_ONLINE[0], status="offline")], DEPLOYMENTS)
    env.commit(env.check()["scan_id"])
    state = json.loads(state_path.read_text())
    state["alerts"]["worker_offline:Trader1"] -= hw.REMIND_SECONDS + 1
    state_path.write_text(json.dumps(state))
    report = env.check()
    assert report["verdict"] == "ALERT"
    assert report["conditions"]["reminders"] == ["worker_offline:Trader1"]


def test_uncommitted_alert_is_raised_again(env):
    env.set_fleet(WORKER_ONLINE, [dict(DEPLOYMENTS[1], status="stopped")])
    assert env.check()["verdict"] == "ALERT"
    assert env.check()["verdict"] == "ALERT"  # agent died before commit: alert again


def test_offline_worker_and_stopped_deployment_alert_without_leaking_token(env):
    env.set_fleet([dict(WORKER_ONLINE[0], status="offline")],
                  [DEPLOYMENTS[0], dict(DEPLOYMENTS[1], status="stopped")])
    report = env.check()
    assert report["verdict"] == "ALERT"
    assert sorted(report["conditions"]["new"]) == [
        "deployment_not_running:4c81384e", "worker_offline:Trader1"]
    assert "Trader1 OFFLINE" in report["verdict_line"]
    assert "deployment 4c81384e rates-trend on Tradier Personal is stopped" in report["verdict_line"]
    assert "SECRET-TOKEN" not in json.dumps(report)


def test_unreadable_fleet_is_an_alert(env):
    env.fleet.write_text("not json")
    report = env.check()
    assert report["conditions"]["new"] == ["fleet_unreadable"]


def test_crash_loop_from_watchdog_log(env):
    now = datetime.now(UTC).astimezone()
    lines = [
        f"{(now - timedelta(minutes=m)).isoformat(timespec='seconds')} health check failed; "
        "restarting coordinator\n"
        for m in (50, 30, 10)
    ]
    env.watchdog_log.write_text("".join(lines))
    report = env.check()
    assert report["watchdog_log"]["restarts_last_hour"] == 3
    assert report["verdict"] == "ALERT"
    assert "crash_loop" in report["conditions"]["new"]
    assert "CRASH LOOP" in report["verdict_line"]


def test_rotated_log_is_rescanned_from_start(env):
    env.append_log("x" * 5000 + "\n")
    env.commit(env.check()["scan_id"])
    env.coord_log.write_text(ERROR_BLOCK)  # smaller than the saved offset
    assert len(env.check()["errors"]["to_file"]) == 1


def test_partial_line_is_left_for_the_next_check(env):
    env.commit(env.check()["scan_id"])
    env.append_log("2026-09-26 10:15:00,123 worker.x ERROR boom while writ")
    assert env.check()["errors"]["to_file"] == []
    env.append_log("ing\n")
    to_file = env.check()["errors"]["to_file"]
    assert [entry["signature"] for entry in to_file] == ["worker.x ERROR boom while writing"]


def test_commit_rejects_unknown_scan(env):
    env.check()
    with pytest.raises(SystemExit):
        env.commit("nope")
