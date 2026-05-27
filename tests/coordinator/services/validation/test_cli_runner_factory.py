"""Verify runner_bootstrap (LIBRARY USE) constructs the service graph.

The CLI no longer consumes runner_bootstrap (it's a thin HTTP client now;
see coordinator/api/routes/research.py). This module remains for programmatic
users who want to run validation lab functions without a running coordinator.
"""
import pytest


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
