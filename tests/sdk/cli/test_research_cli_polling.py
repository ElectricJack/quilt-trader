"""CLI tests for the new poll-until-terminal sweep/walk-forward commands."""
from click.testing import CliRunner
from unittest.mock import AsyncMock, MagicMock
import pytest


def _make_fake_client(*, post_response, get_responses):
    """Build a stub CoordinatorClient with controllable POST + GET responses."""
    c = MagicMock()
    c.post = AsyncMock(return_value=post_response)
    c.get = AsyncMock(side_effect=list(get_responses))
    c.aclose = AsyncMock()
    return c


def test_cli_sweep_polls_until_completed(monkeypatch):
    """Default sweep behavior: POST, then poll until terminal status."""
    from sdk.cli.commands import research as research_mod

    fake_client = _make_fake_client(
        post_response={
            "job_id": "j1", "session_id": 1, "kind": "sweep",
            "status": "queued", "progress_pct": 0.0, "progress_message": None,
            "run_ids": [], "error_message": None,
        },
        get_responses=[
            {"job_id": "j1", "status": "running", "progress_pct": 0.5,
             "progress_message": "Trial 1 of 2", "run_ids": ["r1"], "error_message": None},
            {"job_id": "j1", "status": "completed", "progress_pct": 1.0,
             "progress_message": "Done", "run_ids": ["r1", "r2"], "error_message": None},
        ],
    )
    monkeypatch.setattr(research_mod, "_client", lambda ctx: fake_client)
    monkeypatch.setattr(research_mod, "_poll_sleep_s", 0.0)

    runner = CliRunner()
    result = runner.invoke(research_mod.research_group, [
        "sweep", "--session-id", "1",
    ])
    assert result.exit_code == 0, result.output
    assert "j1" in result.output
    assert "completed" in result.output.lower() or "2" in result.output  # 2 run_ids
    assert fake_client.get.call_count == 2


def test_cli_sweep_no_wait_exits_immediately(monkeypatch):
    """--no-wait prints the job_id and exits without polling."""
    from sdk.cli.commands import research as research_mod

    fake_client = _make_fake_client(
        post_response={
            "job_id": "j-fast", "session_id": 1, "kind": "sweep",
            "status": "queued", "progress_pct": 0.0, "progress_message": None,
            "run_ids": [], "error_message": None,
        },
        get_responses=[],
    )
    monkeypatch.setattr(research_mod, "_client", lambda ctx: fake_client)

    runner = CliRunner()
    result = runner.invoke(research_mod.research_group, [
        "sweep", "--session-id", "1", "--no-wait",
    ])
    assert result.exit_code == 0
    assert "j-fast" in result.output
    fake_client.get.assert_not_called()


def test_cli_walk_forward_polls_until_completed(monkeypatch):
    """Walk-forward command mirrors the sweep polling pattern."""
    from sdk.cli.commands import research as research_mod

    fake_client = _make_fake_client(
        post_response={
            "job_id": "wf-1", "session_id": 2, "kind": "walk-forward",
            "status": "queued", "progress_pct": 0.0, "progress_message": None,
            "run_ids": [], "error_message": None,
        },
        get_responses=[
            {"job_id": "wf-1", "status": "completed", "progress_pct": 1.0,
             "progress_message": "Done", "run_ids": ["oos-1", "oos-2"], "error_message": None},
        ],
    )
    monkeypatch.setattr(research_mod, "_client", lambda ctx: fake_client)
    monkeypatch.setattr(research_mod, "_poll_sleep_s", 0.0)

    runner = CliRunner()
    result = runner.invoke(research_mod.research_group, [
        "walk-forward", "--session-id", "2",
    ])
    assert result.exit_code == 0, result.output
    assert "wf-1" in result.output


def test_cli_sweep_failed_status(monkeypatch):
    """Failed job: command exits 0 (job ran to a terminal state) but prints error."""
    from sdk.cli.commands import research as research_mod

    fake_client = _make_fake_client(
        post_response={
            "job_id": "j-bad", "session_id": 1, "kind": "sweep",
            "status": "queued", "progress_pct": 0.0, "progress_message": None,
            "run_ids": [], "error_message": None,
        },
        get_responses=[
            {"job_id": "j-bad", "status": "failed", "progress_pct": 0.1,
             "progress_message": None, "run_ids": [],
             "error_message": "boom"},
        ],
    )
    monkeypatch.setattr(research_mod, "_client", lambda ctx: fake_client)
    monkeypatch.setattr(research_mod, "_poll_sleep_s", 0.0)

    runner = CliRunner()
    result = runner.invoke(research_mod.research_group, [
        "sweep", "--session-id", "1",
    ])
    assert result.exit_code == 0
    assert "j-bad" in result.output
    assert "failed" in result.output.lower() or "boom" in result.output
