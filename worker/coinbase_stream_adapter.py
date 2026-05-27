"""Coinbase Advanced Trade WebSocket streaming adapter.

Connects to wss://advanced-trade-ws.coinbase.com for real-time crypto
market data. No API key required for public market data channels
(matches, ticker). This makes it the easiest free source for high-quality
crypto tick data.

Symbol conversion: BTCUSD (internal) <-> BTC-USD (Coinbase).
"""
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from worker.broker_adapter import BrokerAdapter, MarketDataStreamHandle

logger = logging.getLogger(__name__)

_WS_URL = "wss://advanced-trade-ws.coinbase.com"


def _to_coinbase_symbol(internal: str) -> str:
    """BTCUSD -> BTC-USD. Assumes 3-char quote currency (USD)."""
    if "-" in internal:
        return internal
    return f"{internal[:-3]}-{internal[-3:]}"


def _from_coinbase_symbol(coinbase: str) -> str:
    """BTC-USD -> BTCUSD."""
    return coinbase.replace("-", "")


class CoinbaseStreamAdapter(BrokerAdapter):
    """Data-only adapter for Coinbase. No API key needed for public market data."""

    def __init__(self, **kwargs) -> None:
        pass  # No credentials needed for public market data

    def get_positions(self):
        raise NotImplementedError("Coinbase adapter is data-only")

    def get_account_info(self):
        raise NotImplementedError("Coinbase adapter is data-only")

    def submit_order(self, *args, **kwargs):
        raise NotImplementedError("Coinbase adapter is data-only")

    def close(self):
        pass

    def start_market_data_stream(
        self, symbols: list[str], on_trade, on_quote, asset_class: str = "crypto",
    ) -> MarketDataStreamHandle:
        from coordinator.services.asset_services import get_default_registry
        registry = get_default_registry()
        for sym in symbols:
            if not registry.supports_provider(sym, "coinbase"):
                raise ValueError(
                    f"Coinbase does not support {sym} (only crypto symbols supported)"
                )
        coinbase_symbols = [_to_coinbase_symbol(s) for s in symbols]
        symbol_map = {_to_coinbase_symbol(s): s for s in symbols}
        return _CoinbaseStreamHandle(
            symbols=coinbase_symbols,
            symbol_map=symbol_map,
            on_trade=on_trade,
            on_quote=on_quote,
        )


class _CoinbaseStreamHandle(MarketDataStreamHandle):
    """WebSocket handle for Coinbase Advanced Trade public data."""

    _BACKOFF_INITIAL_S = 1.0
    _BACKOFF_MAX_S = 30.0

    def __init__(self, symbols, symbol_map, on_trade, on_quote):
        self._symbols = symbols
        self._symbol_map = symbol_map  # coinbase_sym -> internal_sym
        self._on_trade = on_trade
        self._on_quote = on_quote
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="coinbase-stream", daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        import websockets.sync.client as ws_client

        backoff = self._BACKOFF_INITIAL_S
        while not self._stop.is_set():
            try:
                with ws_client.connect(_WS_URL) as ws:
                    # Subscribe to market_trades (trades) + ticker (quotes)
                    ws.send(json.dumps({
                        "type": "subscribe",
                        "product_ids": self._symbols,
                        "channel": "market_trades",
                    }))
                    ws.send(json.dumps({
                        "type": "subscribe",
                        "product_ids": self._symbols,
                        "channel": "ticker",
                    }))
                    logger.info("Coinbase WS connected, subscribed to %s", self._symbols)
                    backoff = self._BACKOFF_INITIAL_S

                    while not self._stop.is_set():
                        try:
                            raw = ws.recv(timeout=5.0)
                        except TimeoutError:
                            continue
                        try:
                            msg = json.loads(raw)
                        except (ValueError, TypeError):
                            continue
                        self._dispatch(msg)

            except Exception:
                if self._stop.is_set():
                    break
                logger.exception("Coinbase stream error; reconnecting in %.1fs", backoff)
                if self._stop.wait(backoff):
                    break
                backoff = min(backoff * 2, self._BACKOFF_MAX_S)

    def _dispatch(self, msg: dict) -> None:
        channel = msg.get("channel")

        if channel == "market_trades":
            # market_trades channel sends {"events": [{"trades": [...]}]}
            for event in (msg.get("events") or []):
                for trade in (event.get("trades") or []):
                    try:
                        coinbase_sym = trade.get("product_id", "")
                        internal_sym = self._symbol_map.get(
                            coinbase_sym, _from_coinbase_symbol(coinbase_sym)
                        )
                        ts_str = trade.get("time")
                        if ts_str:
                            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        else:
                            ts = datetime.now(timezone.utc)
                        self._on_trade({
                            "symbol": internal_sym,
                            "timestamp": ts,
                            "price": float(trade.get("price", 0.0)),
                            "size": float(trade.get("size", 0.0)),
                        })
                    except Exception:
                        logger.exception("Coinbase trade dispatch error")

        elif channel == "ticker":
            # ticker channel sends {"events": [{"tickers": [...]}]}
            for event in (msg.get("events") or []):
                for ticker in (event.get("tickers") or []):
                    try:
                        coinbase_sym = ticker.get("product_id", "")
                        internal_sym = self._symbol_map.get(
                            coinbase_sym, _from_coinbase_symbol(coinbase_sym)
                        )
                        ts = datetime.now(timezone.utc)
                        best_bid = float(ticker.get("best_bid", 0.0) or 0.0)
                        best_ask = float(ticker.get("best_ask", 0.0) or 0.0)
                        self._on_quote({
                            "symbol": internal_sym,
                            "timestamp": ts,
                            "bid": best_bid,
                            "ask": best_ask,
                            "bid_size": float(ticker.get("best_bid_quantity", 0.0) or 0.0),
                            "ask_size": float(ticker.get("best_ask_quantity", 0.0) or 0.0),
                        })
                    except Exception:
                        logger.exception("Coinbase ticker dispatch error")

    def close(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=3.0)
