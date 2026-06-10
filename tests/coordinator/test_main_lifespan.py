"""Integration tests for snapshot wiring at coordinator startup."""
import pytest
from unittest.mock import MagicMock

from coordinator.api.dependencies import get_container
from coordinator.services.cached_snapshot import CachedSnapshot


@pytest.mark.asyncio
async def test_snapshots_attached_to_container_at_startup(test_app):
    """After lifespan startup, both data snapshots are CachedSnapshot instances."""
    container = get_container()
    assert isinstance(container.coverage_snapshot, CachedSnapshot)
    assert isinstance(container.storage_summary_snapshot, CachedSnapshot)


@pytest.mark.asyncio
async def test_download_complete_invalidates_both_snapshots(test_app):
    """Firing _on_download_complete invalidates both data snapshots."""
    container = get_container()

    # Replace the live snapshots with mocks so we can observe .invalidate() calls.
    cov_mock = MagicMock(spec=CachedSnapshot)
    store_mock = MagicMock(spec=CachedSnapshot)
    container.coverage_snapshot = cov_mock
    container.storage_summary_snapshot = store_mock

    # The download_manager lives on app.state (set in lifespan at main.py:365).
    dm = test_app.state.download_manager
    listeners = dm._completion_listeners
    assert listeners, "expected at least one completion listener registered"

    # Fire each listener with a plausible payload. The lifespan registers one
    # listener (_on_download_complete) that fans out to the snapshots + goal
    # processor.
    for cb in listeners:
        cb("polygon", ["AAPL"], status="completed", error_message=None)

    cov_mock.invalidate.assert_called()
    store_mock.invalidate.assert_called()
