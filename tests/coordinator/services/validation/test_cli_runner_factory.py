"""Verify the CLI runner factory actually constructs real services.

We don't run a full backtest here (no algorithm package is loaded). We just
construct the factory and verify it returns a callable that the validation
lab can pass to its orchestrators without raising NotImplementedError.
"""
import pytest


def test_make_cli_runner_factory_returns_callable(tmp_path, monkeypatch):
    monkeypatch.setenv("QUILT_DB_URL", f"sqlite:///{tmp_path / 't.db'}")
    from sdk.cli.commands.research import _make_cli_runner_factory

    factory = _make_cli_runner_factory()
    assert callable(factory)


def test_bootstrap_runner_services_constructs():
    """The bootstrap helper returns a RunnerServices bundle with all pieces."""
    from coordinator.services.runner_bootstrap import bootstrap_runner_services, RunnerServices

    services = bootstrap_runner_services()
    assert isinstance(services, RunnerServices)
    assert services.runner is not None
    assert services.session_factory is not None
    assert services.download_manager is not None
    assert services.data_service is not None
    assert services.coverage_index is not None
