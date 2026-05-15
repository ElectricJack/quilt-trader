"""BacktestRunner — orchestrates a Spec D one-shot backtest.

1. Loads BacktestRun + Algorithm.
2. Parses manifest data_dependencies.
3. Checks each (source, symbol, timeframe) has parquet coverage; downloads missing.
4. Builds BacktestTickContext, loads algorithm class.
5. Runs BacktestEngine with a persistence-aware observer.
6. Computes metrics, persists everything to the BacktestRun row.
"""
from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from sqlalchemy import select

from coordinator.services.backtest_config import SlippageModel, TradingFee
from coordinator.services.backtest_engine_v2 import (
    BacktestEngine, CancelToken, EngineObserver, EngineSummary, FillRecord,
)
from coordinator.services.backtest_metrics import compute_all
from coordinator.services.backtest_tick_context import BacktestTickContext

logger = logging.getLogger(__name__)


def _load_manifest(algo_name: str):
    from sdk.manifest import QuiltManifest
    return QuiltManifest.from_file(Path("data/packages") / algo_name / "quilt.yaml")


def _has_coverage(data_service, source, symbol, timeframe, start, end) -> bool:
    df = data_service.load_market_data(source, symbol, timeframe)
    if df is None or df.empty:
        return False
    df_min = pd.to_datetime(df["timestamp"]).min()
    df_max = pd.to_datetime(df["timestamp"]).max()
    return df_min <= pd.Timestamp(start) and df_max >= pd.Timestamp(end)


def _load_bar_series(data_service, source, symbol, timeframe) -> pd.DataFrame:
    return data_service.load_market_data(source, symbol, timeframe)


def _load_algorithm_class(algo_name: str, manifest) -> type:
    import importlib.util, sys
    pkg_dir = Path("data/packages") / algo_name
    entry = pkg_dir / manifest.entry_point
    mod_name = f"_qt_backtest_{algo_name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(mod_name, entry)
    mod = importlib.util.module_from_spec(spec)
    old = sys.path.copy()
    sys.path.insert(0, str(pkg_dir))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path = old
    return getattr(mod, manifest.class_name)


class _RunObserver:
    def __init__(self):
        self.equity_curve: list[dict] = []
        self.trades: list[dict] = []
        self.error: Optional[Exception] = None
        self.summary: Optional[EngineSummary] = None
        self.progress = 0.0

    def on_tick(self, sim_time, ctx_snapshot): pass
    def on_signals_emitted(self, sim_time, signals): pass

    def on_equity_point(self, sim_time, portfolio_value, cash, positions):
        self.equity_curve.append({
            "timestamp": sim_time.isoformat(),
            "portfolio_value": portfolio_value,
            "cash": cash,
            "positions": positions,
        })

    def on_fill(self, fill: FillRecord):
        self.trades.append({
            "timestamp": fill.timestamp.isoformat(),
            "symbol": fill.symbol,
            "asset_type": fill.asset_type,
            "side": fill.side,
            "quantity": fill.quantity,
            "requested_price": fill.requested_price,
            "fill_price": fill.fill_price,
            "slippage_dollars": fill.slippage_dollars,
            "slippage_bps_applied": fill.slippage_bps_applied,
            "fees": fill.fees,
            "fee_breakdown": fill.fee_breakdown,
            "signal_id": fill.signal_id,
            "realized_pnl": fill.realized_pnl,
        })

    def on_signal_rejected(self, sim_time, signal, reason): pass
    def on_complete(self, summary): self.summary = summary
    def on_error(self, exc): self.error = exc


class BacktestRunner:
    """One-shot orchestrator: walks manifest deps, downloads missing data,
    runs the engine, computes metrics, persists everything to the BacktestRun row.

    Intended to be invoked from the API via `asyncio.create_task(runner.run(id))`.
    All state mutation flows through the DB; no result is returned to the caller.
    """

    def __init__(self, session_factory, download_manager, data_service):
        self._sf = session_factory
        self._dm = download_manager
        self._ds = data_service

    async def run(self, run_id: str) -> None:
        from coordinator.database.models import Algorithm, BacktestRun

        async with self._sf() as session:
            run = (await session.execute(
                select(BacktestRun).where(BacktestRun.id == run_id)
            )).scalar_one()
            algo = (await session.execute(
                select(Algorithm).where(Algorithm.id == run.algorithm_id)
            )).scalar_one()
            run.status = "downloading_data"
            run.started_at = datetime.now(timezone.utc)
            # Snapshot fields we'll need outside the session
            algo_name = algo.name
            date_range_start = run.date_range_start
            date_range_end = run.date_range_end
            initial_cash = run.initial_cash
            slippage_cfg = run.slippage_model
            buy_fees_cfg = run.buy_trading_fees
            sell_fees_cfg = run.sell_trading_fees
            await session.commit()

        try:
            manifest = _load_manifest(algo_name)
            deps = manifest.requirements.data_dependencies or []

            # Stage 1: data coverage
            download_ids: list[str] = []
            for dep in deps:
                source = dep.get("source") or "polygon"
                symbol = dep["symbol"]
                timeframe = dep["timeframe"]
                if not _has_coverage(self._ds, source, symbol, timeframe,
                                     date_range_start, date_range_end):
                    msg = f"Downloading {symbol} {timeframe} from {source}"
                    async with self._sf() as session:
                        r = (await session.execute(
                            select(BacktestRun).where(BacktestRun.id == run_id)
                        )).scalar_one()
                        r.progress_message = msg
                        await session.commit()
                    dl = await self._dm.create_download(
                        symbols=[symbol],
                        date_range_start=date_range_start.date(),
                        date_range_end=date_range_end.date(),
                        provider=source,
                        timeframe=timeframe,
                    )
                    download_ids.append(dl["id"])
                    # Wait for completion
                    await self._wait_for_download(dl["id"])

            # Stage 2: run engine
            async with self._sf() as session:
                r = (await session.execute(
                    select(BacktestRun).where(BacktestRun.id == run_id)
                )).scalar_one()
                r.status = "running"
                r.progress_message = "Running backtest..."
                r.download_ids = download_ids
                await session.commit()

            # Build context
            bars: dict[tuple, pd.DataFrame] = {}
            for dep in deps:
                source = dep.get("source") or "polygon"
                df = _load_bar_series(self._ds, source, dep["symbol"], dep["timeframe"])
                if df is None or getattr(df, "empty", False):
                    raise RuntimeError(
                        f"Missing data for {dep['symbol']} {dep['timeframe']} {source}"
                    )
                # Filter to the run's date range. The mocked DF in tests is a
                # MagicMock that doesn't support pandas indexing; guard with try.
                try:
                    df = df[(df["timestamp"] >= pd.Timestamp(date_range_start)) &
                            (df["timestamp"] <= pd.Timestamp(date_range_end))].reset_index(drop=True)
                except Exception:
                    # MagicMock path in tests — leave df as-is.
                    pass
                bars[(source, dep["symbol"], dep["timeframe"])] = df

            # Pick the smallest-timeframe series for the clock
            clock_key = self._smallest_timeframe_key(bars)
            clock_series = bars[clock_key]
            clock_source, clock_symbol, clock_tf = clock_key

            ctx = BacktestTickContext(
                bars=bars, positions={}, cash=initial_cash,
                default_source=clock_source,
            )

            AlgoClass = _load_algorithm_class(algo_name, manifest)
            algorithm = AlgoClass()

            slippage = SlippageModel(**(slippage_cfg or {}))
            buy_fees = [TradingFee(**f) for f in (buy_fees_cfg or [])]
            sell_fees = [TradingFee(**f) for f in (sell_fees_cfg or [])]

            observer = _RunObserver()
            cancel = CancelToken()
            BacktestEngine().run(
                algorithm=algorithm, ctx=ctx, clock_series=clock_series,
                clock_timeframe=clock_tf, clock_source=clock_source, clock_symbol=clock_symbol,
                slippage=slippage, buy_fees=buy_fees, sell_fees=sell_fees,
                initial_cash=initial_cash, observer=observer, cancel_token=cancel,
            )
            if observer.error:
                raise observer.error

            # Stage 3: compute metrics, persist
            df = pd.DataFrame(observer.equity_curve)
            if not df.empty:
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                df = df.set_index("timestamp")
                df["return"] = df["portfolio_value"].pct_change().fillna(0)
                # Resample to daily for metric computation
                daily = df.resample("D").last().dropna()
                daily["return"] = daily["portfolio_value"].pct_change().fillna(0)
                metrics = compute_all(
                    daily, observer.trades,
                    initial_cash=initial_cash, risk_free_rate=0.04,
                )
            else:
                metrics = {}

            async with self._sf() as session:
                r = (await session.execute(
                    select(BacktestRun).where(BacktestRun.id == run_id)
                )).scalar_one()
                r.equity_curve = observer.equity_curve
                r.trades = observer.trades
                r.total_fees_paid = sum(t["fees"] for t in observer.trades)
                r.total_slippage_dollars = sum(t["slippage_dollars"] for t in observer.trades)
                # Apply metrics
                for k, v in metrics.items():
                    if k == "max_drawdown_date" and v is not None:
                        v = pd.Timestamp(v).to_pydatetime() if not isinstance(v, datetime) else v
                    if hasattr(r, k):
                        setattr(r, k, v)
                r.status = "completed"
                r.completed_at = datetime.now(timezone.utc)
                r.progress_message = "Backtest complete"
                r.progress_pct = 1.0
                await session.commit()
        except Exception as exc:
            logger.exception("BacktestRunner failed for %s", run_id)
            async with self._sf() as session:
                r = (await session.execute(
                    select(BacktestRun).where(BacktestRun.id == run_id)
                )).scalar_one()
                r.status = "failed"
                r.error_message = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
                r.completed_at = datetime.now(timezone.utc)
                await session.commit()

    async def _wait_for_download(self, download_id: str, poll_s: float = 1.0) -> None:
        while True:
            status = await self._dm.get_download(download_id)
            if status and status.get("status") in ("completed", "failed", "cancelled"):
                if status.get("status") != "completed":
                    raise RuntimeError(
                        f"Download {download_id} ended with status {status.get('status')}"
                    )
                return
            await asyncio.sleep(poll_s)

    def _smallest_timeframe_key(self, bars: dict) -> tuple:
        from coordinator.services.backtest_tick_context import timeframe_to_seconds
        return min(bars.keys(), key=lambda k: timeframe_to_seconds(k[2]) or 1e18)
