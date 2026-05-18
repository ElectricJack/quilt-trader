"""ThetaData REST-polling adapter for near-real-time bar data.

ThetaData's live streaming requires their Terminal app (local TCP).
This adapter polls their historical-bars endpoint every poll_interval_s
seconds to fetch the most recent bar, providing a practical (though not
tick-level) live data source.

For true tick streaming, the user runs ThetaData Terminal locally and
a future TCP-based adapter connects to it.
"""
import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

from worker.broker_adapter import BrokerAdapter, MarketDataStreamHandle, OrderResult

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.thetadata.us/v2"


class ThetaDataStreamAdapter(BrokerAdapter):
    """Data-only adapter for ThetaData. Polls REST for latest bars."""

    def __init__(self, username: str, password: str, poll_interval_s: int = 60, **kwargs) -> None:
        self._username = username
        self._password = password
        self._poll_interval_s = poll_interval_s
        self._token: Optional[str] = None

    def _ensure_token(self) -> str:
        if self._token is not None:
            return self._token
        resp = httpx.post(
            f"{_BASE_URL}/auth",
            json={"username": self._username, "password": self._password},
            timeout=10.0,
        )
        resp.raise_for_status()
        self._token = resp.json().get("token") or resp.json().get("access_token")
        if not self._token:
            raise RuntimeError("ThetaData auth returned no token")
        return self._token

    # ── Trading stubs ──
    def get_positions(self):
        raise NotImplementedError("ThetaData is a data-only provider")
    def get_account_info(self):
        raise NotImplementedError("ThetaData is a data-only provider")
    def submit_order(self, *args, **kwargs):
        raise NotImplementedError("ThetaData is a data-only provider")
    def close(self):
        self._token = None

    # ── Streaming (polling) ──
    def start_market_data_stream(
        self, symbols: list[str], on_trade, on_quote, asset_class: str = "equities",
    ) -> MarketDataStreamHandle:
        return _ThetaDataPollHandle(
            username=self._username,
            password=self._password,
            symbols=symbols,
            asset_class=asset_class,
            on_trade=on_trade,
            poll_interval_s=self._poll_interval_s,
        )


class _ThetaDataPollHandle(MarketDataStreamHandle):
    """Polls ThetaData's historical bars endpoint for the latest bar."""

    def __init__(self, username, password, symbols, asset_class, on_trade, poll_interval_s):
        self._username = username
        self._password = password
        self._symbols = symbols
        self._asset_class = asset_class
        self._on_trade = on_trade
        self._poll_interval_s = poll_interval_s
        self._stop = threading.Event()
        self._last_timestamps: dict[str, datetime] = {}
        self._thread = threading.Thread(
            target=self._run, name="thetadata-poll", daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            for symbol in self._symbols:
                try:
                    self._poll_symbol(symbol)
                except Exception:
                    logger.exception("ThetaData poll error for %s", symbol)
            if self._stop.wait(self._poll_interval_s):
                break

    def _poll_symbol(self, symbol: str) -> None:
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y%m%d")
        try:
            with httpx.Client(timeout=15.0) as client:
                # Auth
                auth_resp = client.post(
                    f"{_BASE_URL}/auth",
                    json={"username": self._username, "password": self._password},
                )
                auth_resp.raise_for_status()
                token = auth_resp.json().get("token") or auth_resp.json().get("access_token")
                if not token:
                    logger.warning("ThetaData auth returned no token")
                    return

                # Fetch latest 1-min bars
                resp = client.get(
                    f"{_BASE_URL}/hist/stock/trade",
                    params={
                        "root": symbol,
                        "start_date": today,
                        "end_date": today,
                        "ivl": 60000,  # 1 minute in ms
                    },
                    headers={"Authorization": f"Bearer {token}"},
                )
                if resp.status_code != 200:
                    logger.warning("ThetaData bars response %d for %s", resp.status_code, symbol)
                    return
                data = resp.json()
        except Exception:
            logger.exception("ThetaData HTTP error for %s", symbol)
            return

        # Parse response — ThetaData returns a "response" array of bar objects
        bars = data.get("response") or data.get("data") or []
        if not isinstance(bars, list):
            return

        for bar in bars:
            if not isinstance(bar, dict):
                continue
            ts_ms = bar.get("ms_of_day")
            date_val = bar.get("date")
            if ts_ms is None or date_val is None:
                continue
            try:
                date_str = str(date_val)
                dt = datetime(
                    int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]),
                    tzinfo=timezone.utc,
                ) + timedelta(milliseconds=int(ts_ms))
            except (ValueError, TypeError):
                continue

            # Only emit bars newer than the last one we saw
            last = self._last_timestamps.get(symbol)
            if last is not None and dt <= last:
                continue
            self._last_timestamps[symbol] = dt

            price = float(bar.get("close") or bar.get("last") or 0.0)
            size = float(bar.get("volume") or bar.get("size") or 0.0)
            if price > 0:
                self._on_trade({
                    "symbol": symbol,
                    "timestamp": dt,
                    "price": price,
                    "size": size,
                })

    def close(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=3.0)
