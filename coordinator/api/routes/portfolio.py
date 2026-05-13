from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from coordinator.api.dependencies import get_db
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


@router.get("/equity")
async def portfolio_equity(
    range: RangeLiteral = Query("1m"),
    db: AsyncSession = Depends(get_db),
):
    cutoff = _range_to_cutoff(range)

    accts_result = await db.execute(select(Account))
    accounts = accts_result.scalars().all()

    out = []
    for acct in accounts:
        snap_query = select(AccountSnapshot).where(AccountSnapshot.account_id == acct.id)
        if cutoff is not None:
            snap_query = snap_query.where(AccountSnapshot.timestamp >= cutoff)
        snap_query = snap_query.order_by(AccountSnapshot.timestamp)
        snap_result = await db.execute(snap_query)
        snaps = snap_result.scalars().all()

        if not snaps:
            continue

        out.append({
            "account_id": acct.id,
            "account_name": acct.name,
            "points": [
                {"timestamp": s.timestamp.isoformat(), "value": s.total_value}
                for s in snaps
            ],
        })

    return {"accounts": out}


@router.get("/kpis")
async def portfolio_kpis(db: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Total equity: sum of latest snapshot per account
    accts_result = await db.execute(select(Account))
    accounts = accts_result.scalars().all()
    total_equity = 0.0
    total_cash = 0.0
    total_positions_value = 0.0
    for acct in accounts:
        snap_q = (
            select(AccountSnapshot)
            .where(AccountSnapshot.account_id == acct.id)
            .order_by(AccountSnapshot.timestamp.desc())
            .limit(1)
        )
        snap = (await db.execute(snap_q)).scalar_one_or_none()
        if snap:
            total_equity += snap.total_value
            total_cash += snap.cash
            total_positions_value += snap.positions_value

    # Open positions
    pos_q = select(Position).where(Position.status == "open")
    open_positions = (await db.execute(pos_q)).scalars().all()
    open_risk = sum(p.unrealized_pnl or 0.0 for p in open_positions)

    # Today's trades
    trade_q = select(func.count(TradeLog.id)).where(TradeLog.timestamp >= today_start)
    trades_today = (await db.execute(trade_q)).scalar() or 0

    deployed_pct = (
        (total_positions_value / total_equity * 100.0) if total_equity > 0 else 0.0
    )

    return {
        "total_equity": total_equity,
        "today_pnl": open_risk,  # placeholder: realized today + unrealized delta
        "today_pnl_pct": 0.0,
        "trades_today": trades_today,
        "trades_today_wins": 0,
        "trades_today_losses": 0,
        "win_rate": 0.0,
        "win_rate_7d_avg": 0.0,
        "open_positions": len(open_positions),
        "open_positions_long": sum(1 for p in open_positions if (p.net_cost or 0) >= 0),
        "open_positions_short": sum(1 for p in open_positions if (p.net_cost or 0) < 0),
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


@router.get("/allocation")
async def portfolio_allocation(db: AsyncSession = Depends(get_db)):
    # Cash: sum across latest snapshots per account
    accts = (await db.execute(select(Account))).scalars().all()
    total_cash = 0.0
    for acct in accts:
        snap_q = (
            select(AccountSnapshot)
            .where(AccountSnapshot.account_id == acct.id)
            .order_by(AccountSnapshot.timestamp.desc())
            .limit(1)
        )
        snap = (await db.execute(snap_q)).scalar_one_or_none()
        if snap:
            total_cash += snap.cash

    # Open positions
    pos_q = select(Position).where(Position.status == "open")
    positions = (await db.execute(pos_q)).scalars().all()

    class_totals: dict[str, float] = {}
    symbol_totals: dict[str, float] = {}
    for pos in positions:
        for leg in pos.legs or []:
            asset_class = leg.get("asset_type", "equities")
            symbol = leg.get("symbol", "?")
            value = float(leg.get("value", 0.0))
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
            "color": "#6b7280",
        })
    if cash_value > 0:
        by_symbol.append({
            "key": "Cash",
            "label": "Cash",
            "value_usd": cash_value,
            "percent": round(cash_value / grand * 100.0, 1),
            "color": "#6b7280",
        })

    return {"by_class": by_class, "by_symbol": by_symbol}
