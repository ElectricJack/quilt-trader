import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from coordinator.database.models import AlgorithmInstance, DecisionLog, BacktestComparison
from coordinator.services.backtest_engine import BacktestComparator
from coordinator.services.backtest_config import SlippageModel
from coordinator.services.parallel_backtest_feeder import ParallelBacktestFeeder

try:
    from coordinator.services.backtest_runner import (
        _load_manifest,
        _load_bar_series,
        _load_algorithm_class,
    )
    _RUNNER_AVAILABLE = True
except ImportError:
    _RUNNER_AVAILABLE = False

logger = logging.getLogger(__name__)


class BacktestSchedulerJob:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        comparator: BacktestComparator | None = None,
        lookback_hours: int = 24,
        threshold: float = 5.0,
    ) -> None:
        self._session_factory = session_factory
        self._comparator = comparator or BacktestComparator()
        self._lookback_hours = lookback_hours
        self._threshold = threshold

    async def run(self) -> list[dict]:
        results = []
        async with self._session_factory() as session:
            running = await session.execute(
                select(AlgorithmInstance).where(AlgorithmInstance.status == "running")
            )
            instances = running.scalars().all()

        # Stage 1: run the backtest engine through the feeder to populate
        # DecisionLog(mode="backtest") rows before the comparison reads them.
        if _RUNNER_AVAILABLE:
            for instance in instances:
                try:
                    await self._feed_backtest_decisions(instance)
                except Exception as e:
                    logger.warning(
                        "Parallel backtest feeder failed for instance %s: %s",
                        instance.id, e,
                    )

        # Stage 2: compare live vs backtest decision logs (existing logic).
        for instance in instances:
            try:
                result = await self._compare_instance(instance.id, instance.algorithm_id)
                results.append(result)
            except Exception as e:
                logger.error("Backtest comparison failed for instance %s: %s", instance.id, e)
                results.append({"instance_id": instance.id, "error": str(e)})

        return results

    async def _feed_backtest_decisions(self, instance: AlgorithmInstance) -> None:
        """Run the BacktestEngine through the ParallelBacktestFeeder for one instance."""
        import asyncio

        from coordinator.services.backtest_engine_v2 import BacktestEngine, CancelToken

        cutoff = datetime.now(timezone.utc) - timedelta(hours=self._lookback_hours)

        # Load algorithm class and manifest via C1 helpers.
        manifest = await _load_manifest(instance.algorithm_id, self._session_factory)
        algorithm_class = _load_algorithm_class(manifest)
        algorithm = algorithm_class(config=instance.config_values or {})

        # Load bar data covering the lookback window.
        bar_series, clock_tf, clock_source, clock_symbol = await _load_bar_series(
            manifest=manifest,
            date_start=cutoff,
            date_end=datetime.now(timezone.utc),
            session_factory=self._session_factory,
        )

        from coordinator.services.backtest_tick_context import BacktestTickContext
        ctx = BacktestTickContext(manifest=manifest)

        feeder = ParallelBacktestFeeder(
            instance_id=instance.id,
            session_factory=self._session_factory,
        )

        engine = BacktestEngine()
        cancel = CancelToken()

        # Run synchronously in a thread to avoid blocking the event loop.
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: engine.run(
                algorithm=algorithm,
                ctx=ctx,
                clock_series=bar_series,
                clock_timeframe=clock_tf,
                clock_source=clock_source,
                clock_symbol=clock_symbol,
                slippage=SlippageModel(),
                buy_fees=[],
                sell_fees=[],
                initial_cash=100_000.0,
                observer=feeder,
                cancel_token=cancel,
            ),
        )

    async def _compare_instance(self, instance_id: str, algorithm_id: str) -> dict:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self._lookback_hours)

        async with self._session_factory() as session:
            live_result = await session.execute(
                select(DecisionLog)
                .where(DecisionLog.instance_id == instance_id)
                .where(DecisionLog.mode == "live")
                .where(DecisionLog.timestamp >= cutoff)
                .order_by(DecisionLog.timestamp)
            )
            live_decisions = [
                {
                    "timestamp": d.timestamp.isoformat(),
                    "signals_produced": d.signals_produced or [],
                }
                for d in live_result.scalars().all()
            ]

            bt_result = await session.execute(
                select(DecisionLog)
                .where(DecisionLog.instance_id == instance_id)
                .where(DecisionLog.mode == "backtest")
                .where(DecisionLog.timestamp >= cutoff)
                .order_by(DecisionLog.timestamp)
            )
            bt_decisions = [
                {
                    "timestamp": d.timestamp.isoformat(),
                    "signals_produced": d.signals_produced or [],
                }
                for d in bt_result.scalars().all()
            ]

        if not live_decisions and not bt_decisions:
            return {"instance_id": instance_id, "status": "no_data"}

        comparison = self._comparator.compare(live_decisions, bt_decisions, self._threshold)

        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            record = BacktestComparison(
                instance_id=instance_id,
                algorithm_id=algorithm_id,
                time_range_start=cutoff,
                time_range_end=now,
                total_ticks=comparison.total_ticks,
                matching_ticks=comparison.matching_ticks,
                match_percentage=comparison.match_percentage,
                divergences=comparison.divergences[:50],
                summary=f"{'ALERT: ' if comparison.exceeds_threshold else ''}Match rate: {comparison.match_percentage}%",
            )
            session.add(record)
            await session.commit()

        return {
            "instance_id": instance_id,
            "match_percentage": comparison.match_percentage,
            "exceeds_threshold": comparison.exceeds_threshold,
            "total_ticks": comparison.total_ticks,
        }
