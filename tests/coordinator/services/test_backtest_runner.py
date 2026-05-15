"""Tests for BacktestRunner — Spec D one-shot orchestrator."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from coordinator.api.dependencies import get_container
from coordinator.services.backtest_runner import BacktestRunner


@pytest.mark.asyncio
async def test_runner_creates_row_and_advances_status(test_app, db_session):
    """End-to-end with mocked engine: queued -> downloading_data -> running -> completed."""
    from coordinator.database.models import Algorithm, BacktestRun
    algo = Algorithm(name="test-algo", repo_url="https://example/x", install_status="installed")
    db_session.add(algo); await db_session.flush()

    run = BacktestRun(
        algorithm_id=algo.id,
        date_range_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        date_range_end=datetime(2024, 2, 1, tzinfo=timezone.utc),
        initial_cash=10_000.0,
    )
    db_session.add(run); await db_session.commit()

    # Mock everything that's NOT the runner itself.
    with patch("coordinator.services.backtest_runner._load_manifest") as load_manifest, \
         patch("coordinator.services.backtest_runner._has_coverage", return_value=True) as has_cov, \
         patch("coordinator.services.backtest_runner._load_bar_series") as load_bars, \
         patch("coordinator.services.backtest_runner._load_algorithm_class") as load_class, \
         patch("coordinator.services.backtest_runner.BacktestEngine") as mock_engine_cls:
        load_manifest.return_value = MagicMock(
            requirements=MagicMock(data_dependencies=[
                {"symbol": "SPY", "timeframe": "1day", "source": "polygon"},
            ]),
        )
        # Engine immediately calls observer.on_complete
        def fake_engine_run(**kwargs):
            obs = kwargs["observer"]
            from coordinator.services.backtest_engine_v2 import EngineSummary
            obs.on_equity_point(datetime(2024, 1, 1, tzinfo=timezone.utc), 10_000.0, 10_000.0, [])
            obs.on_complete(EngineSummary(total_bars=10, total_signals=0, total_fills=0,
                                          final_cash=10_000.0, final_portfolio_value=10_000.0))
        mock_engine_cls.return_value.run = fake_engine_run
        load_bars.return_value = MagicMock(empty=False)
        load_class.return_value = MagicMock  # returns the class, instantiation happens inside runner

        container = get_container()
        runner = BacktestRunner(
            session_factory=container.session_factory,
            download_manager=MagicMock(),
            data_service=MagicMock(),
        )
        await runner.run(run.id)

    from sqlalchemy import select
    refreshed = (await db_session.execute(
        select(BacktestRun).where(BacktestRun.id == run.id)
    )).scalar_one()
    await db_session.refresh(refreshed)
    assert refreshed.status == "completed"
