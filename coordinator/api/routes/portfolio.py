from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_container, get_db
from coordinator.api.serialization import to_iso_utc
from coordinator.database.models import Account, AccountSnapshot, Position, TradeLog

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])

RangeLiteral = Literal["1d", "1w", "1m", "all"]


def _range_to_cutoff(rng: str) -> datetime | None:
    now = datetime.now(timezone.utc)
    return {
        "1d": now - timedelta(days=1),
        "1w": now - timedelta(weeks=1),
        "1m": now - timedelta(days=30),
        "all": None,
    }[rng]


async def _visible_accounts(db: AsyncSession) -> list:
    """Return accounts with show_in_overview=True."""
    return (await db.execute(
        select(Account).where(Account.show_in_overview == True)  # noqa: E712
    )).scalars().all()


async def _visible_account_ids(db: AsyncSession) -> list[str]:
    accounts = await _visible_accounts(db)
    return [a.id for a in accounts]


@router.get("/equity")
async def portfolio_equity(
    range: RangeLiteral = Query("1m"),
    db: AsyncSession = Depends(get_db),
):
    import asyncio
    import json as _json
    from coordinator.database.models import AccountEquityDaily
    from worker.adapter_factory import make_broker_adapter

    cutoff = _range_to_cutoff(range)
    accounts = await _visible_accounts(db)
    container = get_container()
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    out = []
    for acct in accounts:
        points = []

        # Historical: materialized daily table
        eq_query = select(AccountEquityDaily).where(AccountEquityDaily.account_id == acct.id)
        if cutoff is not None:
            eq_query = eq_query.where(AccountEquityDaily.date >= cutoff.date())
        eq_query = eq_query.order_by(AccountEquityDaily.date)
        eq_rows = (await db.execute(eq_query)).scalars().all()

        if eq_rows:
            points = [
                {"timestamp": r.date.isoformat() + "T00:00:00Z", "value": r.total_value}
                for r in eq_rows
            ]
        else:
            # Fallback to sparse snapshots for accounts not yet backfilled
            snap_query = select(AccountSnapshot).where(AccountSnapshot.account_id == acct.id)
            if cutoff is not None:
                snap_query = snap_query.where(AccountSnapshot.timestamp >= cutoff)
            snap_query = snap_query.order_by(AccountSnapshot.timestamp)
            snaps = (await db.execute(snap_query)).scalars().all()
            points = [
                {"timestamp": to_iso_utc(s.timestamp), "value": s.total_value}
                for s in snaps
            ]

        # Append live broker value as the current data point
        try:
            creds = _json.loads(container.encryption.decrypt(acct.credentials))
            adapter = make_broker_adapter(acct.broker_type, acct.environment, creds)
            info = await asyncio.to_thread(adapter.get_account_info)
            live_value = float(info.get("portfolio_value", 0))
            if live_value > 0:
                points.append({"timestamp": now_iso, "value": live_value})
        except Exception:
            logger.warning("Failed to fetch live value for %s", acct.name, exc_info=True)

        if points:
            out.append({
                "account_id": acct.id,
                "account_name": acct.name,
                "points": points,
            })

    return {"accounts": out}


@router.get("/kpis")
async def portfolio_kpis(db: AsyncSession = Depends(get_db)):
    from coordinator.database.models import AccountEquityDaily
    from datetime import date as date_type

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = date_type.today() - timedelta(days=1)

    accounts = await _visible_accounts(db)
    visible_ids = [a.id for a in accounts]

    # Fetch live broker values for total equity + positions
    live_positions, total_cash, total_equity = await _fetch_live_positions(accounts)
    total_positions_value = sum(p.get("market_value", 0) for p in live_positions)
    # Use broker's portfolio_value as authoritative total (includes unsettled, margin, etc.)
    # Fall back to positions + cash if broker didn't report portfolio_value
    if total_equity == 0:
        total_equity = total_positions_value + total_cash
    open_risk = await _compute_portfolio_var(live_positions)

    # Today's P&L: live broker value minus most recent materialized close
    prior_total = 0.0
    for acct in accounts:
        eq_q = (
            select(AccountEquityDaily)
            .where(AccountEquityDaily.account_id == acct.id)
            .order_by(AccountEquityDaily.date.desc())
            .limit(1)
        )
        row = (await db.execute(eq_q)).scalar_one_or_none()
        if row:
            prior_total += row.total_value

    today_pnl = total_equity - prior_total if prior_total > 0 else 0.0
    today_pnl_pct = (today_pnl / prior_total * 100.0) if prior_total > 0 else 0.0

    # Today's trades from trade_log
    trade_q = select(func.count(TradeLog.id)).where(
        TradeLog.timestamp >= today_start,
        TradeLog.account_id.in_(visible_ids),
    )
    trades_today = (await db.execute(trade_q)).scalar() or 0

    # Win/loss approximation from sell trades (sells at profit vs loss)
    from coordinator.database.models import Position
    today_sells = (await db.execute(
        select(TradeLog).where(
            TradeLog.timestamp >= today_start,
            TradeLog.account_id.in_(visible_ids),
            TradeLog.side == "sell",
        )
    )).scalars().all()

    today_wins = 0
    today_losses = 0
    for t in today_sells:
        if t.position_id:
            pos = (await db.execute(select(Position).where(Position.id == t.position_id))).scalar_one_or_none()
            if pos and pos.avg_cost and t.filled_price:
                if t.filled_price > pos.avg_cost:
                    today_wins += 1
                elif t.filled_price < pos.avg_cost:
                    today_losses += 1

    all_sell_count = (await db.execute(
        select(func.count(TradeLog.id)).where(
            TradeLog.account_id.in_(visible_ids),
            TradeLog.side == "sell",
        )
    )).scalar() or 0
    win_rate = 0.0
    win_rate_7d = 0.0

    deployed_pct = (
        (total_positions_value / total_equity * 100.0) if total_equity > 0 else 0.0
    )

    return {
        "total_equity": total_equity,
        "today_pnl": today_pnl,
        "today_pnl_pct": today_pnl_pct,
        "trades_today": trades_today,
        "trades_today_wins": today_wins,
        "trades_today_losses": today_losses,
        "win_rate": win_rate,
        "win_rate_7d_avg": win_rate_7d,
        "open_positions": len(live_positions),
        "open_positions_long": sum(1 for p in live_positions if p.get("quantity", 0) > 0),
        "open_positions_short": sum(1 for p in live_positions if p.get("quantity", 0) < 0),
        "open_risk": open_risk,
        "open_risk_pct_equity": (open_risk / total_equity * 100.0) if total_equity > 0 else 0.0,
        "deployed_pct": deployed_pct,
        "deployed_usd": total_positions_value,
        "buying_power": total_cash,
        "buying_power_pct": (total_cash / total_equity * 100.0) if total_equity > 0 else 0.0,
    }


CLASS_COLORS = {
    "equities": "#f59e0b",
    "crypto": "#3b82f6",
    "options": "#8b5cf6",
    "futures": "#ef4444",
    "cash": "#6b7280",
}

SYMBOL_PALETTE = [
    "#f97316", "#3b82f6", "#14b8a6", "#a855f7", "#ec4899",
    "#84cc16", "#06b6d4", "#eab308", "#10b981", "#f43f5e",
]


async def _compute_portfolio_var(
    positions: list[dict],
    confidence: float = 0.95,
    lookback_days: int = 60,
) -> float:
    """Compute daily Value at Risk for the portfolio.

    Uses historical simulation: get daily returns for each held symbol,
    compute portfolio daily returns weighted by position size, take the
    percentile loss.

    Returns dollar VaR (positive number = potential loss).
    """
    import numpy as np
    from coordinator.services.data_service import DataService

    if not positions:
        return 0.0

    ds = DataService(market_data_dir="data/market", custom_data_dir="data/custom")

    symbols = []
    weights = []
    total_value = sum(p.get("market_value", 0) for p in positions)
    if total_value <= 0:
        return 0.0

    # Group positions by symbol (aggregate across accounts)
    sym_values: dict[str, float] = {}
    for p in positions:
        sym = p["symbol"]
        sym_values[sym] = sym_values.get(sym, 0) + p.get("market_value", 0)

    # Load daily returns for each symbol
    returns_matrix = []
    valid_symbols = []
    for sym, value in sym_values.items():
        if value <= 0:
            continue
        # Try multiple providers
        df = None
        for provider in ("polygon", "tradier", "yfinance", "alpaca_live", "tradier_live"):
            df = ds.load_market_data(provider, sym, "1day")
            if df is not None and len(df) >= 10:
                break
        if df is None or len(df) < 10:
            continue

        import pandas as pd
        closes = pd.to_datetime(df["timestamp"])
        close_prices = df["close"].astype(float).values
        if len(close_prices) < 10:
            continue

        # Daily log returns, last N days
        tail = close_prices[-lookback_days:]
        if len(tail) < 10:
            continue
        daily_returns = np.diff(np.log(tail))
        returns_matrix.append(daily_returns)
        valid_symbols.append(sym)
        weights.append(value / total_value)

    if not returns_matrix:
        return 0.0

    # Align lengths (some may differ by a day)
    min_len = min(len(r) for r in returns_matrix)
    returns_matrix = [r[-min_len:] for r in returns_matrix]
    weights = np.array(weights[:len(returns_matrix)])
    weights = weights / weights.sum()

    # Portfolio daily returns = weighted sum
    returns_arr = np.array(returns_matrix)
    portfolio_returns = returns_arr.T @ weights

    # VaR = percentile loss × total portfolio value
    var_pct = np.percentile(portfolio_returns, (1 - confidence) * 100)
    var_dollars = abs(var_pct) * total_value

    return round(var_dollars, 2)


async def _fetch_live_positions(accts) -> tuple[list[dict], float, float]:
    """Fetch live positions from brokers for all visible accounts.
    Returns (positions, total_cash, total_portfolio_value)."""
    import asyncio
    import json as _json
    from worker.adapter_factory import make_broker_adapter
    container = get_container()

    all_positions: list[dict] = []
    total_cash = 0.0
    total_portfolio_value = 0.0
    for acct in accts:
        try:
            creds = _json.loads(container.encryption.decrypt(acct.credentials))
            adapter = make_broker_adapter(acct.broker_type, acct.environment, creds)
            positions = await asyncio.to_thread(adapter.get_positions)
            info = await asyncio.to_thread(adapter.get_account_info)
            total_cash += float(info.get("cash", 0))
            total_portfolio_value += float(info.get("portfolio_value", 0))
            for sym, pos in positions.items():
                all_positions.append({
                    "symbol": sym,
                    "quantity": float(pos.get("quantity", 0)),
                    "market_value": float(pos.get("market_value", 0)),
                    "current_price": float(pos.get("current_price", 0)),
                    "avg_price": float(pos.get("avg_price", 0)),
                    "unrealized_pnl": float(pos.get("unrealized_pnl", 0)),
                    "asset_class": pos.get("asset_class", "equities"),
                    "account_id": acct.id,
                    "account_name": acct.name,
                })
        except Exception:
            logger.warning("Failed to fetch positions for %s", acct.name, exc_info=True)
    return all_positions, total_cash, total_portfolio_value


@router.get("/allocation")
async def portfolio_allocation(db: AsyncSession = Depends(get_db)):
    accts = await _visible_accounts(db)
    all_positions, total_cash, _ = await _fetch_live_positions(accts)

    class_totals: dict[str, float] = {}
    symbol_totals: dict[str, float] = {}
    for pos in all_positions:
        asset_class = pos.get("asset_class", "equities")
        symbol = pos["symbol"]
        value = pos["market_value"]
        class_totals[asset_class] = class_totals.get(asset_class, 0.0) + value
        symbol_totals[symbol] = symbol_totals.get(symbol, 0.0) + value

    if total_cash > 0:
        class_totals["cash"] = total_cash
        symbol_totals["Cash"] = total_cash

    grand = sum(class_totals.values()) or 1.0

    by_class = [
        {
            "key": k,
            "label": k.title(),
            "value_usd": v,
            "percent": round(v / grand * 100.0, 1),
            "color": CLASS_COLORS.get(k, "#6b7280"),
        }
        for k, v in sorted(class_totals.items(), key=lambda x: -x[1])
    ]

    # Top 6 symbols + "More" rollup + cash always last
    cash_value = symbol_totals.pop("Cash", 0.0)
    ranked = sorted(symbol_totals.items(), key=lambda x: -x[1])
    top = ranked[:6]
    rest = ranked[6:]
    by_symbol = [
        {
            "key": sym,
            "label": sym,
            "value_usd": v,
            "percent": round(v / grand * 100.0, 1),
            "color": SYMBOL_PALETTE[i % len(SYMBOL_PALETTE)],
        }
        for i, (sym, v) in enumerate(top)
    ]
    if rest:
        rest_total = sum(v for _, v in rest)
        by_symbol.append({
            "key": "_more",
            "label": f"+{len(rest)} more",
            "value_usd": rest_total,
            "percent": round(rest_total / grand * 100.0, 1),
            "color": "#475569",
        })
    if cash_value > 0:
        by_symbol.append({
            "key": "Cash",
            "label": "Cash",
            "value_usd": cash_value,
            "percent": round(cash_value / grand * 100.0, 1),
            "color": "#22d3ee",
        })

    return {"by_class": by_class, "by_symbol": by_symbol}
