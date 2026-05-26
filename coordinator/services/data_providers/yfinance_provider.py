"""Yahoo Finance data provider via yfinance.

Historical-only provider — data is delayed 15-20 minutes so this is
NOT suitable for live trading. Excellent for backtesting indices (VIX,
SPX), equities, crypto, and option chains that other providers gate
behind paid tiers.

Supports daily and intraday bars. Intraday (1min) is limited to the
last 7 days by Yahoo Finance.
"""
import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

PageCallback = Callable[[int, int, "float | None"], Awaitable[None]]
StatusCallback = Callable[[str], Awaitable[None]]
BarsCallback = Callable[[list[dict]], Awaitable[None]]

SYMBOL_MAP = {
    "VIX": "^VIX",
    "SPX": "^GSPC",
    "NDX": "^IXIC",
    "RUT": "^RUT",
    "DJI": "^DJI",
    "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",
    "SOLUSD": "SOL-USD",
}

TIMEFRAME_MAP = {
    "1min": "1m",
    "5min": "5m",
    "15min": "15m",
    "1hour": "1h",
    "1day": "1d",
}


class YFinanceProvider:
    """Historical data provider using Yahoo Finance (yfinance).

    Delayed data only — do not use for live trading.
    No API key required.
    """

    def __init__(self) -> None:
        pass

    async def _safe_status(self, on_status: StatusCallback | None, msg: str) -> None:
        if on_status is None:
            return
        try:
            await on_status(msg)
        except Exception:
            logger.exception("on_status callback raised")

    def _map_symbol(self, symbol: str) -> str:
        return SYMBOL_MAP.get(symbol, symbol)

    async def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        start: date,
        end: date,
        on_page: PageCallback | None = None,
        on_status: StatusCallback | None = None,
        on_bars: BarsCallback | None = None,
    ) -> list[dict]:
        yf_interval = TIMEFRAME_MAP.get(timeframe)
        if yf_interval is None:
            raise ValueError(f"Unsupported timeframe: {timeframe}. Supported: {list(TIMEFRAME_MAP.keys())}")

        yf_symbol = self._map_symbol(symbol)
        await self._safe_status(on_status, f"Downloading {yf_symbol} {timeframe} from Yahoo Finance")

        def _download():
            import yfinance as yf
            ticker = yf.Ticker(yf_symbol)
            df = ticker.history(
                start=start.isoformat(),
                end=end.isoformat(),
                interval=yf_interval,
                auto_adjust=False,
            )
            return df

        df = await asyncio.to_thread(_download)

        if df is None or df.empty:
            await self._safe_status(on_status, f"No data returned for {yf_symbol}")
            return []

        bars = []
        for ts, row in df.iterrows():
            bar = {
                "timestamp": ts.isoformat(),
                "open": float(row.get("Open", 0)),
                "high": float(row.get("High", 0)),
                "low": float(row.get("Low", 0)),
                "close": float(row.get("Close", 0)),
                "volume": int(row.get("Volume", 0)),
            }
            bars.append(bar)

        if on_bars is not None and bars:
            await on_bars(bars)
        if on_page is not None:
            await on_page(0, len(bars), 1.0)

        await self._safe_status(on_status, f"Downloaded {len(bars)} bars for {yf_symbol}")
        return bars
