import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from collections.abc import Callable
from typing import Any, Optional

import pandas as pd
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from coordinator.database.models import MarketDataDownload
from coordinator.services.data_service import DataService

logger = logging.getLogger(__name__)


class DownloadManager:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        data_service: DataService,
        providers: dict[str, Any],
        on_download_complete: Callable[[str, list[str]], None] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._data_service = data_service
        self._providers = providers
        self._on_download_complete = on_download_complete
        self._active_tasks: dict[str, asyncio.Task] = {}
        # Serialize downloads: only one runs at a time so we don't fan out parallel
        # API requests and trip provider rate limits. Queued downloads wait inside
        # their own task on this semaphore and remain cancellable while waiting.
        self._download_semaphore = asyncio.Semaphore(1)

    async def create_download(
        self,
        symbols: list[str],
        date_range_start: date,
        date_range_end: date,
        provider: str = "polygon",
        data_type: str = "bars",
        timeframe: str = "1day",
    ) -> dict:
        if provider not in self._providers:
            raise ValueError(f"Unknown provider: {provider}. Available: {list(self._providers.keys())}")

        async with self._session_factory() as session:
            download = MarketDataDownload(
                symbols=symbols,
                date_range_start=date_range_start,
                date_range_end=date_range_end,
                provider=provider,
                data_type=data_type,
                timeframe=timeframe,
                status="queued",
                progress_current=0,
                progress_total=len(symbols),
            )
            session.add(download)
            await session.commit()
            download_id = download.id

        task = asyncio.create_task(self._run_download(download_id))
        self._active_tasks[download_id] = task
        task.add_done_callback(lambda t: self._active_tasks.pop(download_id, None))

        return {"id": download_id, "status": "queued", "symbols": symbols, "total": len(symbols)}

    async def get_download(self, download_id: str) -> Optional[dict]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(MarketDataDownload).where(MarketDataDownload.id == download_id)
            )
            dl = result.scalar_one_or_none()
            if dl is None:
                return None
            return self._to_dict(dl)

    async def list_downloads(self) -> list[dict]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(MarketDataDownload).order_by(MarketDataDownload.started_at.desc())
            )
            return [self._to_dict(dl) for dl in result.scalars().all()]

    async def delete_download(self, download_id: str) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(
                select(MarketDataDownload).where(MarketDataDownload.id == download_id)
            )
            dl = result.scalar_one_or_none()
            if dl is None:
                return False
            await session.delete(dl)
            await session.commit()
        return True

    async def clear_downloads(self, statuses: Optional[list[str]] = None) -> int:
        """Delete completed/failed/cancelled downloads. Always preserves active rows."""
        async with self._session_factory() as session:
            query = select(MarketDataDownload).where(
                ~MarketDataDownload.status.in_(["queued", "running"])
            )
            if statuses:
                query = query.where(MarketDataDownload.status.in_(statuses))
            rows = (await session.execute(query)).scalars().all()
            count = 0
            for r in rows:
                await session.delete(r)
                count += 1
            await session.commit()
            return count

    async def recover_orphaned_downloads(self) -> int:
        """Mark any DB row stuck in 'queued' or 'running' as 'failed'.

        Called at startup. Any row in those states must be an orphan because
        we just constructed this DownloadManager — no tasks have been registered yet.
        Returns the count of rows marked.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(MarketDataDownload).where(
                    MarketDataDownload.status.in_(["queued", "running"])
                )
            )
            orphans = result.scalars().all()
            count = 0
            for row in orphans:
                row.status = "failed"
                row.completed_at = datetime.now(timezone.utc)
                row.error_message = "Orphaned by coordinator restart"
                row.progress_message = None
                count += 1
            await session.commit()
            return count

    async def shutdown(self) -> None:
        """Cancel all live download tasks. Safe to call multiple times."""
        tasks = list(self._active_tasks.values())
        for t in tasks:
            if not t.done():
                t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._active_tasks.clear()

    async def cancel_download(self, download_id: str) -> bool:
        """Cancel an active task. If the DB row exists but no in-memory task
        is registered, mark it as cancelled directly (orphan case)."""
        task = self._active_tasks.get(download_id)
        if task and not task.done():
            task.cancel()
            async with self._session_factory() as session:
                await session.execute(
                    update(MarketDataDownload)
                    .where(MarketDataDownload.id == download_id)
                    .values(status="cancelled", completed_at=datetime.now(timezone.utc))
                )
                await session.commit()
            return True

        # No live task — check if there's an orphan row
        async with self._session_factory() as session:
            result = await session.execute(
                select(MarketDataDownload).where(MarketDataDownload.id == download_id)
            )
            dl = result.scalar_one_or_none()
            if dl is None:
                return False
            if dl.status in {"queued", "running"}:
                dl.status = "cancelled"
                dl.completed_at = datetime.now(timezone.utc)
                dl.error_message = "Cancelled (orphan; no live task)"
                dl.progress_message = None
                await session.commit()
                return True
            # Already in a terminal state — nothing to do
            return False

    async def _run_download(self, download_id: str) -> None:
        # Wait our turn — only one download fetches at a time. The row stays
        # status="queued" while we wait; this task is still cancellable.
        async with self._download_semaphore:
            async with self._session_factory() as session:
                result = await session.execute(
                    select(MarketDataDownload).where(MarketDataDownload.id == download_id)
                )
                dl = result.scalar_one_or_none()
                if dl is None:
                    return
                # If a concurrent cancel flipped status while we were queued, bail
                # out before doing any work.
                if dl.status != "queued":
                    return

                symbols = dl.symbols
                provider_name = dl.provider
                data_type = dl.data_type
                timeframe = dl.timeframe
                start = dl.date_range_start
                end = dl.date_range_end

                await session.execute(
                    update(MarketDataDownload)
                    .where(MarketDataDownload.id == download_id)
                    .values(status="running", started_at=datetime.now(timezone.utc))
                )
                await session.commit()
            await self._run_download_body(
                download_id, symbols, provider_name, data_type, timeframe, start, end,
            )

    async def _run_download_body(
        self,
        download_id: str,
        symbols: list[str],
        provider_name: str,
        data_type: str,
        timeframe: str,
        start: date,
        end: date,
    ) -> None:

        provider = self._providers[provider_name]
        errors = []
        any_bars_saved = False

        async def _update_progress_message(message: str) -> None:
            async with self._session_factory() as session:
                await session.execute(
                    update(MarketDataDownload)
                    .where(MarketDataDownload.id == download_id)
                    .values(progress_message=message)
                )
                await session.commit()

        for i, symbol in enumerate(symbols):
            # Reset current_symbol_pct before starting each new symbol
            async with self._session_factory() as session:
                await session.execute(
                    update(MarketDataDownload)
                    .where(MarketDataDownload.id == download_id)
                    .values(current_symbol_pct=None)
                )
                await session.commit()

            try:
                if data_type != "bars":
                    raise NotImplementedError(
                        f"data_type '{data_type}' is not yet supported by provider '{provider_name}'"
                    )

                async def on_page(page_idx: int, total_bars: int, fraction: float | None = None, sym: str = symbol) -> None:
                    parts = [f"{sym}: page {page_idx + 1}", f"{total_bars:,} bars"]
                    if fraction is not None:
                        parts.append(f"{int(fraction * 100)}% of range")
                    msg = ", ".join(parts)
                    async with self._session_factory() as session:
                        await session.execute(
                            update(MarketDataDownload)
                            .where(MarketDataDownload.id == download_id)
                            .values(progress_message=msg, current_symbol_pct=fraction)
                        )
                        await session.commit()

                async def on_status(msg: str, sym: str = symbol) -> None:
                    await _update_progress_message(f"{sym}: {msg}")

                incremental_saved = False

                async def on_bars(page_bars: list[dict], sym: str = symbol) -> None:
                    nonlocal incremental_saved
                    if not page_bars:
                        return
                    # Persist each page to disk as it arrives so a cancel or crash
                    # mid-pagination still leaves earlier pages on disk for resume.
                    df = pd.DataFrame(page_bars)
                    await asyncio.to_thread(
                        self._data_service.save_market_data,
                        provider_name, sym, timeframe, df,
                    )
                    incremental_saved = True

                # Resume: if data already covers part of the requested range, advance
                # the start date so we only fetch the remainder. dedup in
                # save_market_data handles any small overlap on the boundary day.
                effective_start = start
                latest = self._data_service.latest_market_data_timestamp(
                    provider_name, symbol, timeframe
                )
                if latest is not None:
                    latest_date = latest.date()
                    if latest_date >= end:
                        await _update_progress_message(
                            f"{symbol}: already up to date (through {latest_date.isoformat()})"
                        )
                        # Skip the provider call entirely; existing data covers the range.
                        async with self._session_factory() as session:
                            await session.execute(
                                update(MarketDataDownload)
                                .where(MarketDataDownload.id == download_id)
                                .values(progress_current=i + 1, current_symbol_pct=None)
                            )
                            await session.commit()
                        continue
                    if latest_date >= start:
                        effective_start = latest_date + timedelta(days=1)
                        await _update_progress_message(
                            f"{symbol}: resuming from {effective_start.isoformat()}"
                        )

                await _update_progress_message(f"{symbol}: starting…")
                bars = await provider.fetch_bars(
                    symbol, timeframe, effective_start, end,
                    on_page=on_page, on_status=on_status, on_bars=on_bars,
                )
                if bars or incremental_saved:
                    if bars and not incremental_saved:
                        df = pd.DataFrame(bars)
                        await asyncio.to_thread(
                            self._data_service.save_market_data,
                            provider_name, symbol, timeframe, df,
                        )
                    any_bars_saved = True
                    bar_count = len(bars) if bars else 0
                    logger.info("Downloaded %d bars for %s/%s", bar_count, symbol, timeframe)
                    await _update_progress_message(f"{symbol}: saved {bar_count:,} bars")
                else:
                    logger.warning("No data returned for %s/%s/%s (%s to %s)", provider_name, symbol, timeframe, effective_start, end)
                    errors.append(f"{symbol}: no data returned by {provider_name} for {effective_start} to {end}")
            except NotImplementedError as e:
                logger.warning("%s", e)
                errors.append(f"{symbol}: {e}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Failed to download %s: %s", symbol, e)
                errors.append(f"{symbol}: {e}")

            async with self._session_factory() as session:
                await session.execute(
                    update(MarketDataDownload)
                    .where(MarketDataDownload.id == download_id)
                    .values(progress_current=i + 1)
                )
                await session.commit()

        if not errors:
            final_status = "completed"
        elif len(errors) == len(symbols):
            final_status = "failed"
        else:
            final_status = "completed_with_errors"
        error_msg = "; ".join(errors) if errors else None

        async with self._session_factory() as session:
            await session.execute(
                update(MarketDataDownload)
                .where(MarketDataDownload.id == download_id)
                .values(
                    status=final_status,
                    completed_at=datetime.now(timezone.utc),
                    error_message=error_msg,
                    progress_message=None,
                    current_symbol_pct=None,
                )
            )
            await session.commit()

        logger.info("Download %s finished: %s", download_id, final_status)

        if self._on_download_complete and final_status in ("completed", "completed_with_errors"):
            try:
                self._on_download_complete(provider_name, symbols)
            except Exception:
                logger.exception("on_download_complete callback failed")

    @staticmethod
    def _to_dict(dl: MarketDataDownload) -> dict:
        return {
            "id": dl.id,
            "symbols": dl.symbols,
            "date_range_start": dl.date_range_start.isoformat() if dl.date_range_start else None,
            "date_range_end": dl.date_range_end.isoformat() if dl.date_range_end else None,
            "provider": dl.provider,
            "data_type": dl.data_type,
            "timeframe": dl.timeframe,
            "status": dl.status,
            "progress_current": dl.progress_current,
            "progress_total": dl.progress_total,
            "error_message": dl.error_message,
            "progress_message": dl.progress_message,
            "current_symbol_pct": dl.current_symbol_pct,
            "started_at": dl.started_at.isoformat() if dl.started_at else None,
            "completed_at": dl.completed_at.isoformat() if dl.completed_at else None,
        }
