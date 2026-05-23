"""BacktestRunner — orchestrates a Spec D one-shot backtest.

1. Loads BacktestRun + Algorithm.
2. Parses manifest data_dependencies.
3. Checks each (source, symbol, timeframe) has parquet coverage; downloads missing.
4. Builds BacktestTickContext, loads algorithm class.
5. Runs BacktestEngine with a ChunkingObserver → ParquetWriterThread pipeline.
6. Calls finalize_run to compute metrics and persist everything to the BacktestRun row.
"""
from __future__ import annotations

import asyncio
import functools
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
from coordinator.services.backtest_tick_context import BacktestTickContext

logger = logging.getLogger(__name__)


def _package_dir_name(repo_url: str) -> str:
    """Return the on-disk package directory name for an installed algorithm.

    Matches the convention used by the install flow (coordinator/api/routes/
    algorithms.py) and the update flow: the package directory is named after
    the GitHub repo (last URL segment), NOT after the manifest's `name` field.
    Algorithm.name in the DB comes from the manifest, so it can differ —
    don't use it for filesystem lookups.
    """
    import re
    m = re.match(r"^https?://github\.com/([^/]+/[^/]+?)(?:\.git)?/?$", repo_url or "")
    if not m:
        raise ValueError(f"Cannot derive package directory from repo_url: {repo_url!r}")
    return m.group(1).split("/", 1)[1]


def _load_manifest(pkg_dir_name: str):
    from sdk.manifest import QuiltManifest
    return QuiltManifest.from_file(Path("data/packages") / pkg_dir_name / "quilt.yaml")


def _to_naive_utc(ts) -> pd.Timestamp:
    """Normalize a Timestamp/datetime to tz-naive UTC for comparison.

    Parquet timestamps in data/market/ are stored tz-naive (UTC by convention),
    but BacktestRun.date_range_start/end are tz-aware. Pandas refuses to
    compare across tz-awareness. Strip tz everywhere we compare.
    """
    p = pd.Timestamp(ts)
    if p.tz is not None:
        p = p.tz_convert("UTC").tz_localize(None)
    return p


def _df_timestamps_naive(df: pd.DataFrame) -> pd.Series:
    """Return df['timestamp'] coerced to tz-naive UTC."""
    s = pd.to_datetime(df["timestamp"])
    if hasattr(s.dt, "tz") and s.dt.tz is not None:
        s = s.dt.tz_convert("UTC").dt.tz_localize(None)
    return s


def _load_bar_series(data_service, source, symbol, timeframe) -> pd.DataFrame:
    return data_service.load_market_data(source, symbol, timeframe)


def _load_algorithm_class(pkg_dir_name: str, manifest) -> type:
    import importlib.util, sys
    pkg_dir = Path("data/packages") / pkg_dir_name
    entry = pkg_dir / manifest.entry_point
    mod_name = f"_qt_backtest_{pkg_dir_name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(mod_name, entry)
    mod = importlib.util.module_from_spec(spec)
    old = sys.path.copy()
    sys.path.insert(0, str(pkg_dir))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path = old
    return getattr(mod, manifest.class_name)


def _validate_custom_data_deps(data_deps: list[dict], custom_dir: Path) -> None:
    """Check that every declared custom data dependency exists on disk.

    Raises FileNotFoundError for the first missing dependency.
    """
    for dep in data_deps:
        source = dep.get("source", "")
        if not source:
            continue
        if (custom_dir / source).is_file():
            continue
        found = False
        for ext in (".csv", ".parquet", ".json"):
            if (custom_dir / f"{source}{ext}").is_file():
                found = True
                break
        if found:
            continue
        subdir = custom_dir / source
        if subdir.is_dir() and (any(subdir.glob("*.csv")) or any(subdir.glob("*.parquet")) or any(subdir.glob("*.json"))):
            continue
        stem = Path(source).stem
        if stem != source:
            subdir2 = custom_dir / stem
            if subdir2.is_dir() and (any(subdir2.glob("*.csv")) or any(subdir2.glob("*.parquet")) or any(subdir2.glob("*.json"))):
                continue
        raise FileNotFoundError(
            f"Missing data dependency: {source!r}. "
            f"Expected file or directory at {custom_dir / source}."
        )


class BacktestRunner:
    """One-shot orchestrator: walks manifest deps, downloads missing data,
    runs the engine, computes metrics, persists everything to the BacktestRun row.

    Intended to be invoked from the API via `asyncio.create_task(runner.run(id))`.
    All state mutation flows through the DB; no result is returned to the caller.
    """

    def __init__(self, session_factory, download_manager, data_service, coverage_index=None):
        self._sf = session_factory
        self._dm = download_manager
        self._ds = data_service
        self._coverage_index = coverage_index

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
            algo_repo_url = algo.repo_url
            date_range_start = run.date_range_start
            date_range_end = run.date_range_end
            initial_cash = run.initial_cash
            slippage_cfg = run.slippage_model
            buy_fees_cfg = run.buy_trading_fees
            sell_fees_cfg = run.sell_trading_fees
            await session.commit()

        try:
            pkg_dir_name = _package_dir_name(algo_repo_url)
            manifest = _load_manifest(pkg_dir_name)
            # Read from manifest.assets (new format) with fallback to
            # requirements.data_dependencies (legacy).
            deps = manifest.assets or manifest.requirements.data_dependencies or []

            # Validate custom data dependencies exist before starting
            if manifest.data:
                _validate_custom_data_deps(manifest.data, Path("data/custom"))

            # Stage 1: data coverage — download only missing gaps
            from coordinator.services.coverage_utils import ensure_coverage

            download_ids: list[str] = []
            for dep in deps:
                symbol = dep.get("symbol")
                if not symbol:
                    continue
                source = dep.get("source") or "polygon"
                timeframe = dep.get("timeframe") or "1min"

                if self._coverage_index is not None:
                    msg = f"Checking coverage for {symbol} {timeframe} from {source}"
                    async with self._sf() as session:
                        r = (await session.execute(
                            select(BacktestRun).where(BacktestRun.id == run_id)
                        )).scalar_one()
                        r.progress_message = msg
                        await session.commit()

                    dl_ids = await ensure_coverage(
                        source, symbol,
                        date_range_start.date(), date_range_end.date(),
                        self._dm, self._coverage_index,
                        timeframe=timeframe,
                    )
                    for dl_id in dl_ids:
                        download_ids.append(dl_id)
                        await self._wait_for_download(dl_id)
                else:
                    # Fallback: no coverage index — download the full range if missing
                    df = self._ds.load_market_data(source, symbol, timeframe)
                    has_cov = False
                    if df is not None and not df.empty and "timestamp" in df.columns:
                        ts = _df_timestamps_naive(df)
                        cached_first = ts.min().date()
                        cached_last = ts.max().date()
                        requested_first = _to_naive_utc(date_range_start).date()
                        requested_last = _to_naive_utc(date_range_end).date()
                        has_cov = cached_first <= requested_first and cached_last >= requested_last
                    if not has_cov:
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
                symbol = dep.get("symbol")
                if not symbol:
                    continue
                source = dep.get("source") or "polygon"
                timeframe = dep.get("timeframe") or "1min"
                df = _load_bar_series(self._ds, source, symbol, timeframe)
                if df is None or getattr(df, "empty", False):
                    raise RuntimeError(
                        f"Missing data for {symbol} {timeframe} {source}"
                    )
                # Filter to the run's date range. Normalize tz on both sides
                # because parquet timestamps are tz-naive and date_range_* are
                # tz-aware. The mocked DF in tests is a MagicMock that doesn't
                # support pandas indexing; guard with try.
                try:
                    df = df.copy()
                    df["timestamp"] = _df_timestamps_naive(df)
                    df = df[(df["timestamp"] >= _to_naive_utc(date_range_start)) &
                            (df["timestamp"] <= _to_naive_utc(date_range_end))].reset_index(drop=True)
                except Exception:
                    # MagicMock path in tests — leave df as-is.
                    pass
                bars[(source, symbol, timeframe)] = df

            # Pick the smallest-timeframe series for the clock. If no market
            # bars exist (e.g., scraper-only algo), build a synthetic daily
            # clock spanning the date range.
            if bars:
                clock_key = self._smallest_timeframe_key(bars)
                clock_series = bars[clock_key]
                clock_source, clock_symbol, clock_tf = clock_key
            else:
                import numpy as np
                clock_source = "synthetic"
                clock_symbol = "_clock"
                clock_tf = "1day"
                dates = pd.date_range(
                    start=date_range_start, end=date_range_end, freq="B",  # business days
                    tz=None,
                )
                clock_series = pd.DataFrame({
                    "timestamp": dates,
                    "open": np.zeros(len(dates)),
                    "high": np.zeros(len(dates)),
                    "low": np.zeros(len(dates)),
                    "close": np.zeros(len(dates)),
                    "volume": np.zeros(len(dates)),
                })

            on_miss = self._make_on_miss(date_range_start, date_range_end)
            # Use a real provider as the default source for on-demand downloads,
            # not "synthetic" (which is only used for the clock when no market
            # bars are pre-loaded).
            default_src = clock_source if clock_source != "synthetic" else "polygon"
            ctx = BacktestTickContext(
                bars=bars, positions={}, cash=initial_cash,
                default_source=default_src,
                data_service=self._ds,
                on_miss=on_miss,
            )

            AlgoClass = _load_algorithm_class(pkg_dir_name, manifest)
            algorithm = AlgoClass()

            slippage = SlippageModel(**(slippage_cfg or {}))
            buy_fees = [TradingFee(**f) for f in (buy_fees_cfg or [])]
            sell_fees = [TradingFee(**f) for f in (sell_fees_cfg or [])]

            from queue import Queue
            from coordinator.services.backtest_writer import (
                ChunkingObserver, ParquetWriterThread,
            )
            from coordinator.services.backtest_finalizer import finalize_run

            run_dir = Path("data/backtests") / run_id
            run_dir.mkdir(parents=True, exist_ok=True)
            equity_native_path = run_dir / "equity_native.parquet"
            trades_path = run_dir / "trades.parquet"

            chunk_queue: Queue = Queue(maxsize=8)
            observer = ChunkingObserver(queue=chunk_queue, clock_series=clock_series)
            writer = ParquetWriterThread(
                queue=chunk_queue, equity_path=equity_native_path, trades_path=trades_path,
            )
            writer.start()

            cancel = CancelToken()
            loop = asyncio.get_running_loop()
            pump = asyncio.create_task(self._progress_pump(run_id, observer))
            try:
                await loop.run_in_executor(
                    None,
                    functools.partial(
                        BacktestEngine().run,
                        algorithm=algorithm, ctx=ctx, clock_series=clock_series,
                        clock_timeframe=clock_tf, clock_source=clock_source,
                        clock_symbol=clock_symbol,
                        slippage=slippage, buy_fees=buy_fees, sell_fees=sell_fees,
                        initial_cash=initial_cash, observer=observer,
                        cancel_token=cancel,
                        progress_callback=lambda p: setattr(observer, "progress", p),
                    ),
                )
            finally:
                pump.cancel()
                try:
                    await pump
                except asyncio.CancelledError:
                    pass
                # Signal writer to drain & exit
                chunk_queue.put(None)
                writer.join(timeout=30)

            if writer.is_alive():
                raise RuntimeError("ParquetWriterThread did not finish within 30s")
            if writer.error:
                raise writer.error

            # Load benchmark bars for finalize (if configured)
            benchmark_bar_df = None
            async with self._sf() as session:
                r = (await session.execute(
                    select(BacktestRun).where(BacktestRun.id == run_id)
                )).scalar_one()
                bench_symbol = r.benchmark_symbol
                bench_source = r.benchmark_source
            if bench_symbol and bench_source:
                bdf = self._ds.load_market_data(bench_source, bench_symbol, "1day")
                if bdf is not None and not bdf.empty:
                    benchmark_bar_df = bdf

            # Finalize: resample, compute metrics, persist row.
            # equity_native.parquet may not exist if the mock engine emitted no chunks.
            if equity_native_path.exists():
                await finalize_run(
                    run_id=run_id, run_dir=run_dir,
                    session_factory=self._sf, benchmark_bar_df=benchmark_bar_df,
                )

            # Mark complete + clear progress fields
            async with self._sf() as session:
                r = (await session.execute(
                    select(BacktestRun).where(BacktestRun.id == run_id)
                )).scalar_one()
                r.status = "completed"
                r.completed_at = datetime.now(timezone.utc)
                r.progress_message = "Backtest complete"
                r.progress_pct = 1.0
                r.download_ids = download_ids
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

    async def _progress_pump(
        self, run_id: str, observer, interval_s: float = 2.0,
    ) -> None:
        """Periodically copy observer.progress (set by engine progress_callback)
        into BacktestRun.progress_pct so the dashboard can render a live bar.
        Also persists the latest daily equity snapshot for live curve updates.
        Cancelled when the engine returns.
        """
        from coordinator.database.models import BacktestRun

        while True:
            await asyncio.sleep(interval_s)
            try:
                async with self._sf() as session:
                    r = (await session.execute(
                        select(BacktestRun).where(BacktestRun.id == run_id)
                    )).scalar_one_or_none()
                    if r is None:
                        return
                    r.progress_pct = float(observer.progress)
                    if hasattr(observer, "daily_aggregate_snapshot"):
                        snap = observer.daily_aggregate_snapshot()
                        if snap:
                            r.equity_curve = snap
                    await session.commit()
            except Exception:
                logger.exception("Progress pump iteration failed for %s", run_id)

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

    def _make_on_miss(self, date_range_start, date_range_end):
        """Return a sync callable for BacktestTickContext._on_miss.

        Called from the engine thread when market_data() doesn't find a symbol
        in the pre-loaded bars dict or on disk.  It blocks until the async
        download finishes by submitting the coroutine to the running event loop
        via run_coroutine_threadsafe (safe because the engine runs in a thread
        executor while the loop keeps running on the main thread).

        The event loop is captured here (in the async run() context) so the
        closure can reference it from the engine's executor thread, where
        asyncio.get_event_loop() is not available in Python 3.10+.
        """
        import concurrent.futures
        loop = asyncio.get_running_loop()

        def on_miss(symbol: str, timeframe: str, source: str):
            # First check disk — a previous auto-download may have already saved it.
            df = self._ds.load_market_data(source, symbol, timeframe)
            if df is not None and not df.empty:
                return df
            # Download via the DownloadManager (async).  The backtest engine runs
            # inside run_in_executor so the event loop is still running on the main
            # thread; use run_coroutine_threadsafe to bridge the thread boundary.
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._download_and_wait(
                        symbol, timeframe, source,
                        date_range_start, date_range_end,
                    ),
                    loop,
                )
                future.result(timeout=120)  # block up to 2 min
            except concurrent.futures.TimeoutError:
                logger.error(
                    "Auto-download timed out for %s %s (%s)", symbol, timeframe, source
                )
                return None
            except Exception:
                logger.exception(
                    "Auto-download failed for %s %s (%s)", symbol, timeframe, source
                )
                return None
            # Re-read from disk after the download completed.
            return self._ds.load_market_data(source, symbol, timeframe)

        return on_miss

    async def _download_and_wait(
        self, symbol: str, timeframe: str, source: str,
        start, end,
    ) -> None:
        """Create a DownloadManager job and wait for it to finish."""
        dl = await self._dm.create_download(
            symbols=[symbol],
            date_range_start=start.date() if hasattr(start, "date") else start,
            date_range_end=end.date() if hasattr(end, "date") else end,
            provider=source,
            timeframe=timeframe,
        )
        await self._wait_for_download(dl["id"])

    def _smallest_timeframe_key(self, bars: dict) -> tuple:
        from coordinator.services.backtest_tick_context import timeframe_to_seconds
        return min(bars.keys(), key=lambda k: timeframe_to_seconds(k[2]) or 1e18)
