"""Account backfill service — replay broker transactions into a position ledger
and materialize daily equity values from historical prices."""

from __future__ import annotations

import copy
import logging
import os
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


def _option_multiplier(symbol: str | None) -> int:
    """Return the contract multiplier for a symbol. US equity options (OCC format) = 100."""
    return 100 if symbol and len(symbol) > 15 else 1


def _occ_expiration(symbol: str) -> date | None:
    """Parse expiration date from an OCC option symbol.

    OCC format: SYMBOL + YYMMDD + C/P + strike*1000 (8 digits)
    Example: SPY241008C00574000 → 2024-10-08
    """
    if len(symbol) <= 15:
        return None
    try:
        tail = symbol[-15:]  # YYMMDDX00000000
        yy, mm, dd = int(tail[0:2]), int(tail[2:4]), int(tail[4:6])
        return date(2000 + yy, mm, dd)
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------------------
# 1. replay_transactions
# ---------------------------------------------------------------------------

def replay_transactions(
    transactions: list[dict],
    starting_cash: float = 0.0,
) -> tuple[dict[date, dict[str, dict]], dict[date, float]]:
    """Replay a chronological list of transaction dicts into a position ledger.

    Returns:
        ledger:       {date: {symbol: {"quantity": float, "avg_cost": float}}}
        cash_by_date: {date: float}
    """
    positions: dict[str, dict] = {}  # symbol -> {"quantity", "avg_cost"}
    cash = starting_cash

    ledger: dict[date, dict[str, dict]] = {}
    cash_by_date: dict[date, float] = {}

    for txn in transactions:
        ts = txn["timestamp"]
        if isinstance(ts, str):
            txn_date = datetime.fromisoformat(ts.replace("Z", "+00:00")).date()
        elif isinstance(ts, datetime):
            txn_date = ts.date()
        else:
            txn_date = ts

        txn_type = txn["type"]

        if txn_type == "fill":
            symbol = txn["symbol"]
            side = txn.get("side")
            qty = float(txn.get("quantity") or 0)
            price = float(txn.get("price") or 0)

            if not symbol or qty == 0:
                continue

            mult = _option_multiplier(symbol)

            if side == "buy":
                pos = positions.get(symbol)
                if pos is None:
                    positions[symbol] = {"quantity": qty, "avg_cost": price}
                else:
                    total_qty = pos["quantity"] + qty
                    if total_qty > 0:
                        pos["avg_cost"] = (
                            (pos["avg_cost"] * pos["quantity"] + price * qty) / total_qty
                        )
                    pos["quantity"] = total_qty
                cash -= qty * price * mult
            elif side == "sell":
                pos = positions.get(symbol)
                if pos is not None:
                    pos["quantity"] -= qty
                    cash += qty * price * mult
                    # Remove near-zero positions
                    if abs(pos["quantity"]) < 0.001:
                        del positions[symbol]

        elif txn_type in ("deposit", "dividend", "interest"):
            cash += txn["amount"]

        elif txn_type in ("withdrawal", "fee"):
            cash -= txn["amount"]

        # Remove expired options as of this date
        expired = [
            sym for sym in positions
            if (_exp := _occ_expiration(sym)) is not None and _exp < txn_date
        ]
        for sym in expired:
            del positions[sym]

        # Snapshot after processing this transaction
        ledger[txn_date] = copy.deepcopy(positions)
        cash_by_date[txn_date] = cash

    return ledger, cash_by_date


# ---------------------------------------------------------------------------
# 2. forward_fill_ledger
# ---------------------------------------------------------------------------

def forward_fill_ledger(
    ledger: dict[date, dict[str, dict]],
    cash_by_date: dict[date, float],
    start: date,
    end: date,
) -> tuple[dict[date, dict[str, dict]], dict[date, float]]:
    """Fill weekday gaps between transaction dates by carrying forward.

    Returns new dicts covering every weekday in [start, end].
    """
    filled_ledger: dict[date, dict[str, dict]] = {}
    filled_cash: dict[date, float] = {}

    last_positions: dict[str, dict] = {}
    last_cash: float = 0.0

    current = start
    while current <= end:
        # Skip weekends (5=Sat, 6=Sun)
        if current.weekday() < 5:
            if current in ledger:
                last_positions = ledger[current]
                last_cash = cash_by_date[current]
            filled_ledger[current] = copy.deepcopy(last_positions)
            filled_cash[current] = last_cash
        current += timedelta(days=1)

    return filled_ledger, filled_cash


# ---------------------------------------------------------------------------
# 2b. calibrate_cash_backwards
# ---------------------------------------------------------------------------

def calibrate_cash_backwards(
    cash_by_date: dict[date, float],
    transactions: list[dict],
    actual_cash_today: float,
) -> dict[date, float]:
    """Recompute cash balances by anchoring at today's known broker cash
    and walking backwards through transactions.

    This avoids compounding errors from forward replay — each day's error
    is bounded to that day's transactions instead of accumulating.
    """
    if not cash_by_date:
        return cash_by_date

    sorted_dates = sorted(cash_by_date.keys())

    daily_deltas: dict[date, float] = {}
    for txn in transactions:
        ts = txn.get("timestamp", "")
        if not ts:
            continue
        txn_date = datetime.fromisoformat(ts.replace("Z", "+00:00")).date()
        delta = 0.0
        txn_type = txn.get("type", "")
        if txn_type == "fill":
            qty = float(txn.get("quantity") or 0)
            price = float(txn.get("price") or 0)
            symbol = txn.get("symbol", "")
            mult = _option_multiplier(symbol)
            side = txn.get("side", "")
            if side == "buy":
                delta = -(qty * price * mult)
            elif side == "sell":
                delta = qty * price * mult
        elif txn_type in ("deposit", "dividend", "interest"):
            delta = float(txn.get("amount") or 0)
        elif txn_type in ("withdrawal", "fee"):
            delta = -abs(float(txn.get("amount") or 0))

        daily_deltas[txn_date] = daily_deltas.get(txn_date, 0.0) + delta

    calibrated: dict[date, float] = {}
    cash = actual_cash_today

    for d in reversed(sorted_dates):
        calibrated[d] = cash
        cash -= daily_deltas.get(d, 0.0)

    return calibrated


# ---------------------------------------------------------------------------
# 3. materialize_equity
# ---------------------------------------------------------------------------

def materialize_equity(
    ledger: dict[date, dict[str, dict]],
    cash_by_date: dict[date, float],
    prices: dict[tuple[str, date], float],
) -> list[dict]:
    """Join position ledger against daily close prices to produce equity rows.

    Forward-fills missing prices per symbol; marks those days as estimated.
    Falls back to avg_cost if a symbol price was never seen.
    """
    sorted_dates = sorted(ledger.keys())
    # Track last-known price per symbol for forward-filling
    last_known_price: dict[str, float] = {}
    rows: list[dict] = []

    for d in sorted_dates:
        positions = ledger[d]
        cash = cash_by_date.get(d, 0.0)
        positions_value = 0.0
        estimated = False

        for symbol, pos in positions.items():
            qty = pos["quantity"]
            key = (symbol, d)
            if key in prices:
                price = prices[key]
                last_known_price[symbol] = price
            elif symbol in last_known_price:
                price = last_known_price[symbol]
                estimated = True
            else:
                # Never seen — fall back to avg_cost
                price = pos["avg_cost"]
                estimated = True

            positions_value += qty * price * _option_multiplier(symbol)

        total_value = positions_value + cash
        rows.append({
            "date": d,
            "total_value": total_value,
            "positions_value": positions_value,
            "cash": cash,
            "estimated": estimated,
        })

    return rows


# ---------------------------------------------------------------------------
# 4. load_prices_for_symbols (async)
# ---------------------------------------------------------------------------

async def load_prices_for_symbols(
    symbols: list[str],
    start: date,
    end: date,
    data_service,
    default_provider: str = "alpaca",
) -> dict[tuple[str, date], float]:
    """Load daily close prices from parquet files on disk.

    For each symbol, tries the default provider first, then scans other
    non-live providers in the market data directory.
    """
    result: dict[tuple[str, date], float] = {}

    for symbol in symbols:
        df = _try_load_symbol(data_service, default_provider, symbol)

        if df is None:
            # Scan other providers
            market_dir = data_service._market_dir
            if os.path.isdir(market_dir):
                for provider_name in sorted(os.listdir(market_dir)):
                    if provider_name == default_provider:
                        continue
                    if provider_name.endswith("_live"):
                        continue
                    provider_path = os.path.join(market_dir, provider_name)
                    if not os.path.isdir(provider_path):
                        continue
                    df = _try_load_symbol(data_service, provider_name, symbol)
                    if df is not None:
                        break

        if df is not None:
            _extract_prices(df, symbol, start, end, result)

    return result


def _try_load_symbol(data_service, provider: str, symbol: str) -> Optional[pd.DataFrame]:
    """Attempt to load 1day data for a symbol from a provider."""
    try:
        return data_service.load_market_data(provider, symbol, "1day")
    except Exception:
        logger.debug("Failed to load %s/%s/1day", provider, symbol, exc_info=True)
        return None


def _extract_prices(
    df: pd.DataFrame,
    symbol: str,
    start: date,
    end: date,
    out: dict[tuple[str, date], float],
) -> None:
    """Extract (symbol, date) -> close_price pairs within the date range."""
    if "timestamp" not in df.columns or "close" not in df.columns:
        return

    for _, row in df.iterrows():
        ts = row["timestamp"]
        if isinstance(ts, str):
            d = datetime.fromisoformat(ts.replace("Z", "+00:00")).date()
        elif isinstance(ts, datetime):
            d = ts.date()
        elif isinstance(ts, pd.Timestamp):
            d = ts.date()
        else:
            d = ts

        if start <= d <= end:
            out[(symbol, d)] = float(row["close"])
