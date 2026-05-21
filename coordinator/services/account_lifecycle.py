"""Account lifecycle service — periodic sync, daily close, initial backfill.

Background jobs that keep account data fresh:
- initial_backfill: full history replay for newly added accounts
- periodic_sync: pull new transactions since last sync (every 15 min M-F)
- daily_close: append today's closing equity row (4:35 PM ET M-F)
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, func, select

from coordinator.database.models import (
    Account,
    AccountCashFlow,
    AccountEquityDaily,
    AccountPositionLedger,
    TradeLog,
)
from coordinator.services.account_backfill import (
    forward_fill_ledger,
    load_prices_for_symbols,
    materialize_equity,
    replay_transactions,
)

logger = logging.getLogger(__name__)

# How far back to pull transaction history for a brand-new account
_BACKFILL_START = datetime(2020, 1, 1, tzinfo=timezone.utc)

# Default lookback when no prior sync exists
_DEFAULT_SYNC_LOOKBACK_DAYS = 30


class AccountLifecycleService:
    def __init__(
        self,
        session_factory,
        encryption,
        data_service,
        download_manager,
        ws_manager,
        default_provider: str = "tradier",
    ):
        self._session_factory = session_factory
        self._encryption = encryption
        self._data_service = data_service
        self._download_manager = download_manager
        self._ws_manager = ws_manager
        self._default_provider = default_provider

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    async def _push_progress(self, account_id: str, message: str) -> None:
        target = f"account:{account_id}:setup_progress"
        try:
            await self._ws_manager.broadcast_to_target(
                target, {"type": "setup_progress", "message": message}
            )
        except Exception:
            logger.debug("Failed to push progress for %s", account_id, exc_info=True)

    def _make_adapter(self, account: Account):
        """Create a broker adapter from an Account row."""
        from worker.adapter_factory import make_broker_adapter

        creds = json.loads(self._encryption.decrypt(account.credentials))
        return make_broker_adapter(account.broker_type, account.environment, creds)

    @staticmethod
    def _normalize_transaction(txn) -> dict:
        """Convert a BrokerTransaction dataclass to a plain dict for replay."""
        return {
            "type": txn.type,
            "timestamp": txn.timestamp.isoformat(),
            "symbol": txn.symbol,
            "side": txn.side,
            "quantity": txn.quantity,
            "price": txn.price,
            "amount": txn.amount,
        }

    # ------------------------------------------------------------------
    # initial_backfill
    # ------------------------------------------------------------------

    async def initial_backfill(self, account_id: str) -> None:
        """Full backfill for a newly added account."""
        logger.info("Starting initial backfill for account %s", account_id)
        await self._push_progress(account_id, "Loading account...")

        # 1. Load account, create broker adapter
        async with self._session_factory() as session:
            account = (
                await session.execute(
                    select(Account).where(Account.id == account_id)
                )
            ).scalar_one_or_none()
            if account is None:
                logger.error("Account %s not found for backfill", account_id)
                return
            # Detach the fields we need before closing the session
            broker_type = account.broker_type
            environment = account.environment
            credentials_enc = account.credentials

        creds = json.loads(self._encryption.decrypt(credentials_enc))
        from worker.adapter_factory import make_broker_adapter

        adapter = make_broker_adapter(broker_type, environment, creds)

        # 2. Pull full transaction history
        await self._push_progress(account_id, "Pulling transaction history...")
        since = _BACKFILL_START
        transactions = await asyncio.to_thread(adapter.get_transactions, since)
        logger.info(
            "Backfill: fetched %d transactions for account %s",
            len(transactions),
            account_id,
        )

        if not transactions:
            await self._push_progress(account_id, "No transactions found — done.")
            return

        # 3. Normalize to dicts
        await self._push_progress(account_id, "Replaying transactions...")
        txn_dicts = [self._normalize_transaction(t) for t in transactions]

        # 4. Replay
        ledger, cash_by_date = replay_transactions(txn_dicts, starting_cash=0.0)

        # 5. Forward-fill
        sorted_dates = sorted(ledger.keys())
        start_date = sorted_dates[0]
        end_date = date.today() - timedelta(days=1)
        filled_ledger, filled_cash = forward_fill_ledger(
            ledger, cash_by_date, start_date, end_date
        )

        # 6. Collect all historically-held symbols
        all_symbols: set[str] = set()
        for day_positions in filled_ledger.values():
            all_symbols.update(day_positions.keys())

        # 7. Trigger download of missing price data
        if all_symbols and self._download_manager is not None:
            await self._push_progress(
                account_id,
                f"Downloading price data for {len(all_symbols)} symbol(s)...",
            )
            try:
                await self._download_manager.create_download(
                    symbols=sorted(all_symbols),
                    date_range_start=start_date,
                    date_range_end=end_date,
                    provider=self._default_provider,
                    timeframe="1day",
                )
            except Exception:
                logger.warning(
                    "Download request failed for backfill (provider may not be configured)",
                    exc_info=True,
                )

        # 8. Load prices
        await self._push_progress(account_id, "Loading price data...")
        prices = await load_prices_for_symbols(
            symbols=sorted(all_symbols),
            start=start_date,
            end=end_date,
            data_service=self._data_service,
            default_provider=self._default_provider,
        )

        # 9. Materialize equity
        await self._push_progress(account_id, "Computing equity curve...")
        equity_rows = materialize_equity(filled_ledger, filled_cash, prices)

        # 10. Write to DB
        await self._push_progress(account_id, "Saving to database...")
        async with self._session_factory() as session:
            # Clear existing rows for this account
            await session.execute(
                delete(AccountPositionLedger).where(
                    AccountPositionLedger.account_id == account_id
                )
            )
            await session.execute(
                delete(AccountEquityDaily).where(
                    AccountEquityDaily.account_id == account_id
                )
            )

            # Write ledger rows
            for d, positions in filled_ledger.items():
                for symbol, pos in positions.items():
                    session.add(
                        AccountPositionLedger(
                            account_id=account_id,
                            date=d,
                            symbol=symbol,
                            quantity=pos["quantity"],
                            avg_cost=pos["avg_cost"],
                        )
                    )

            # Write equity rows
            for row in equity_rows:
                session.add(
                    AccountEquityDaily(
                        account_id=account_id,
                        date=row["date"],
                        total_value=row["total_value"],
                        positions_value=row["positions_value"],
                        cash=row["cash"],
                        estimated=row["estimated"],
                    )
                )

            await session.commit()

        # 11. Done
        await self._push_progress(account_id, "Backfill complete.")
        logger.info("Backfill complete for account %s", account_id)

    # ------------------------------------------------------------------
    # periodic_sync
    # ------------------------------------------------------------------

    async def periodic_sync(self) -> None:
        """Sync all accounts — pull new transactions since last sync."""
        logger.debug("Running periodic account sync")
        async with self._session_factory() as session:
            accounts = (
                await session.execute(select(Account))
            ).scalars().all()

        for account in accounts:
            try:
                await self._sync_one(account)
            except Exception:
                logger.error(
                    "periodic_sync failed for account %s (%s)",
                    account.id,
                    account.name,
                    exc_info=True,
                )

    async def _sync_one(self, account: Account) -> None:
        adapter = self._make_adapter(account)

        # Find latest known timestamp for this account
        async with self._session_factory() as session:
            last_trade_ts = (
                await session.execute(
                    select(func.max(TradeLog.timestamp)).where(
                        TradeLog.account_id == account.id
                    )
                )
            ).scalar()
            last_cf_ts = (
                await session.execute(
                    select(func.max(AccountCashFlow.timestamp)).where(
                        AccountCashFlow.account_id == account.id
                    )
                )
            ).scalar()

        latest = None
        if last_trade_ts and last_cf_ts:
            latest = max(last_trade_ts, last_cf_ts)
        else:
            latest = last_trade_ts or last_cf_ts

        if latest is None:
            since = datetime.now(timezone.utc) - timedelta(days=_DEFAULT_SYNC_LOOKBACK_DAYS)
        else:
            # Ensure timezone-aware
            if latest.tzinfo is None:
                latest = latest.replace(tzinfo=timezone.utc)
            since = latest

        # Pull new transactions from broker
        transactions = await asyncio.to_thread(adapter.get_transactions, since)
        if not transactions:
            return

        # Dedup: collect existing broker_txn_ids
        async with self._session_factory() as session:
            existing_trade_ids = set(
                (
                    await session.execute(
                        select(TradeLog.broker_txn_id).where(
                            TradeLog.account_id == account.id,
                            TradeLog.broker_txn_id.isnot(None),
                        )
                    )
                ).scalars().all()
            )
            existing_cf_ids = set(
                (
                    await session.execute(
                        select(AccountCashFlow.broker_txn_id).where(
                            AccountCashFlow.account_id == account.id,
                            AccountCashFlow.broker_txn_id.isnot(None),
                        )
                    )
                ).scalars().all()
            )

        new_trades = 0
        new_cash_flows = 0

        async with self._session_factory() as session:
            for txn in transactions:
                if txn.type == "fill":
                    if txn.broker_id in existing_trade_ids:
                        continue
                    session.add(
                        TradeLog(
                            account_id=account.id,
                            source="sync",
                            timestamp=txn.timestamp,
                            symbol=txn.symbol or "",
                            side=txn.side or "buy",
                            quantity=txn.quantity or 0.0,
                            filled_price=txn.price or 0.0,
                            fees=txn.fees,
                            broker_txn_id=txn.broker_id,
                        )
                    )
                    new_trades += 1
                elif txn.type in ("deposit", "withdrawal", "dividend", "interest", "fee"):
                    if txn.broker_id in existing_cf_ids:
                        continue
                    session.add(
                        AccountCashFlow(
                            account_id=account.id,
                            type=txn.type,
                            amount=txn.amount,
                            timestamp=txn.timestamp,
                            broker_txn_id=txn.broker_id,
                        )
                    )
                    new_cash_flows += 1

            await session.commit()

        if new_trades or new_cash_flows:
            logger.info(
                "Synced account %s (%s): %d new trade(s), %d new cash flow(s)",
                account.id,
                account.name,
                new_trades,
                new_cash_flows,
            )

    # ------------------------------------------------------------------
    # daily_close
    # ------------------------------------------------------------------

    async def daily_close(self) -> None:
        """Append today's closing row to account_equity_daily for each account."""
        logger.debug("Running daily close")
        today = date.today()

        async with self._session_factory() as session:
            accounts = (
                await session.execute(select(Account))
            ).scalars().all()

        for account in accounts:
            try:
                await self._close_one(account, today)
            except Exception:
                logger.error(
                    "daily_close failed for account %s (%s)",
                    account.id,
                    account.name,
                    exc_info=True,
                )

    async def _close_one(self, account: Account, today: date) -> None:
        adapter = self._make_adapter(account)

        # Get current portfolio state from broker
        account_info = await asyncio.to_thread(adapter.get_account_info)
        positions = await asyncio.to_thread(adapter.get_positions)

        total_value = account_info.get("portfolio_value", 0.0)
        cash = account_info.get("cash", 0.0)
        positions_value = sum(
            p.get("market_value", 0.0) for p in positions.values()
        )

        # Upsert: check if row exists for (account_id, today)
        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    select(AccountEquityDaily).where(
                        AccountEquityDaily.account_id == account.id,
                        AccountEquityDaily.date == today,
                    )
                )
            ).scalar_one_or_none()

            if existing is not None:
                existing.total_value = total_value
                existing.positions_value = positions_value
                existing.cash = cash
                existing.estimated = False
            else:
                session.add(
                    AccountEquityDaily(
                        account_id=account.id,
                        date=today,
                        total_value=total_value,
                        positions_value=positions_value,
                        cash=cash,
                        estimated=False,
                    )
                )

            await session.commit()

        logger.info(
            "Daily close for %s (%s): total=%.2f, positions=%.2f, cash=%.2f",
            account.id,
            account.name,
            total_value,
            positions_value,
            cash,
        )
