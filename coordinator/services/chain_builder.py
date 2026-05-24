"""Build an option chain DataFrame from individual contract bar files.

The chain is a point-in-time cross-section: for each contract, take the
most recent bar at or before `as_of` and extract pricing.  Bid/ask are
estimated from the bar's high-low spread, matching the Polygon provider's
existing convention.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

import pandas as pd

_OCC_RE = re.compile(r"^(?:O:)?([A-Z]{1,6})(\d{6})([CP])(\d{8})$")

CHAIN_COLUMNS = [
    "symbol", "strike", "option_type", "bid", "ask",
    "last", "volume", "open_interest", "implied_volatility",
]


def parse_occ_symbol(symbol: str) -> Optional[dict]:
    m = _OCC_RE.match(symbol)
    if not m:
        return None
    underlying, date_str, cp, strike_raw = m.groups()
    yy, mm, dd = int(date_str[:2]), int(date_str[2:4]), int(date_str[4:6])
    raw = symbol.removeprefix("O:")
    return {
        "underlying": underlying,
        "expiration": f"20{yy:02d}-{mm:02d}-{dd:02d}",
        "option_type": "call" if cp == "C" else "put",
        "strike": int(strike_raw) / 1000.0,
        "raw_symbol": raw,
    }


def build_chain_from_bars(
    bars: dict[str, pd.DataFrame],
    as_of: pd.Timestamp,
    underlying_price: float | None = None,
    risk_free_rate: float = 0.04,
) -> pd.DataFrame:
    if not bars:
        return pd.DataFrame(columns=CHAIN_COLUMNS)

    from coordinator.services.options_math import bs_iv, estimate_spread
    from datetime import date as _date

    rows: list[dict] = []
    for symbol, df in bars.items():
        parsed = parse_occ_symbol(symbol)
        if parsed is None:
            continue
        if df is None or df.empty:
            continue
        ts = pd.to_datetime(df["timestamp"])
        if ts.dt.tz is not None:
            ts = ts.dt.tz_convert("UTC").dt.tz_localize(None)
        cutoff = as_of.tz_localize(None) if as_of.tz is not None else as_of
        visible = df[ts <= cutoff]
        if visible.empty:
            continue
        last_bar = visible.iloc[-1]
        close = float(last_bar["close"])
        vol = int(last_bar.get("volume", 0))

        # Use real bid/ask if present in bar data, otherwise estimate from volume
        if "bid" in last_bar.index and "ask" in last_bar.index and pd.notna(last_bar["bid"]) and pd.notna(last_bar["ask"]):
            bid = float(last_bar["bid"])
            ask = float(last_bar["ask"])
        else:
            spread = estimate_spread(close, vol)
            bid = max(0.0, close - spread / 2)
            ask = close + spread / 2

        # Compute implied volatility if we have enough info
        iv = 0.0
        exp_date = _date.fromisoformat(parsed["expiration"])
        as_of_date = cutoff.date() if hasattr(cutoff, "date") else cutoff
        days_to_exp = (exp_date - as_of_date).days
        T = max(days_to_exp, 0) / 365.0
        if T > 0 and close > 0 and underlying_price is not None and underlying_price > 0:
            computed_iv = bs_iv(
                price=close, S=underlying_price, K=parsed["strike"],
                T=T, r=risk_free_rate, option_type=parsed["option_type"],
            )
            if computed_iv is not None:
                iv = computed_iv

        rows.append({
            "symbol": parsed["raw_symbol"],
            "strike": parsed["strike"],
            "option_type": parsed["option_type"],
            "bid": bid,
            "ask": ask,
            "last": close,
            "volume": vol,
            "open_interest": 0,
            "implied_volatility": iv,
        })

    if not rows:
        return pd.DataFrame(columns=CHAIN_COLUMNS)
    return pd.DataFrame(rows)
