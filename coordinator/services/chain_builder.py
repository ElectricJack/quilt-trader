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
) -> pd.DataFrame:
    if not bars:
        return pd.DataFrame(columns=CHAIN_COLUMNS)

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
        high = float(last_bar["high"])
        low = float(last_bar["low"])
        vol = int(last_bar.get("volume", 0))
        spread = max((high - low) * 0.1, close * 0.02) if close > 0 else 0.1
        rows.append({
            "symbol": parsed["raw_symbol"],
            "strike": parsed["strike"],
            "option_type": parsed["option_type"],
            "bid": max(0.0, close - spread / 2),
            "ask": close + spread / 2,
            "last": close,
            "volume": vol,
            "open_interest": 0,
            "implied_volatility": 0.0,
        })

    if not rows:
        return pd.DataFrame(columns=CHAIN_COLUMNS)
    return pd.DataFrame(rows)
