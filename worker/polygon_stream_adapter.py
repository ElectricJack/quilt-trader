"""Polygon.io real-time WebSocket streaming adapter.

Connects to wss://socket.polygon.io/{cluster} where cluster is
'crypto' or 'stocks'. Auth via API key. Subscribes to trades (XT.*)
and quotes (XQ.*).

Requires a paid Polygon plan — free tier rejects the WS auth.
"""
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from worker.broker_adapter import BrokerAdapter, MarketDataStreamHandle, OrderResult

logger = logging.getLogger(__name__)

_WS_BASE = "wss://socket.polygon.io"


class PolygonStreamAdapter(BrokerAdapter):
    """Data-only adapter for Polygon.io. Implements streaming; trading methods raise."""

    def __init__(self, api_key: str, **kwargs) -> None:
        self._api_key = api_key

    # ── Trading stubs (not supported) ──
    def get_positions(self):
        raise NotImplementedError("Polygon is a data-only provider")
    def get_account_info(self):
        raise NotImplementedError("Polygon is a data-only provider")
    def submit_order(self, *args, **kwargs):
        raise NotImplementedError("Polygon is a data-only provider")
    def close(self):
        pass

    # ── Streaming ──
    def start_market_data_stream(
        self, symbols: list[str], on_trade, on_quote, asset_class: str = "equities",
    ) -> MarketDataStreamHandle:
        from coordinator.services.asset_services import get_default_registry
        registry = get_default_registry()
        if symbols:
            cfg = registry.stream_config(symbols[0], "polygon")
            cluster = cfg.cluster or "stocks"
        else:
            cluster = "stocks"
        url = f"{_WS_BASE}/{cluster}"
        return _PolygonStreamHandle(
            url=url,
            api_key=self._api_key,
            symbols=symbols,
            asset_class=asset_class,
            on_trade=on_trade,
            on_quote=on_quote,
        )


class _PolygonStreamHandle(MarketDataStreamHandle):
    """WebSocket stream handle for Polygon real-time data."""

    _BACKOFF_INITIAL_S = 1.0
    _BACKOFF_MAX_S = 30.0

    def __init__(self, url, api_key, symbols, asset_class, on_trade, on_quote):
        self._url = url
        self._api_key = api_key
        self._symbols = symbols
        self._asset_class = asset_class
        self._on_trade = on_trade
        self._on_quote = on_quote
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="polygon-stream", daemon=True
        )
        self._thread.start()

    def _format_symbol(self, symbol: str) -> str:
        """Convert internal symbol to Polygon stream format via registry."""
        from coordinator.services.asset_services import get_default_registry
        cfg = get_default_registry().stream_config(symbol, "polygon")
        if cfg.symbol_transform == "polygon_x_prefix":
            return f"X:{symbol}"
        if cfg.symbol_transform == "occ_prefix":
            return f"O:{symbol.removeprefix('O:')}"
        return symbol

    def _normalize_symbol(self, polygon_sym: str) -> str:
        """Convert Polygon symbol back to internal format.
        X:BTCUSD -> BTCUSD
        """
        if polygon_sym.startswith("X:"):
            return polygon_sym[2:]
        return polygon_sym

    def _run(self) -> None:
        import websockets.sync.client as ws_client

        backoff = self._BACKOFF_INITIAL_S
        while not self._stop.is_set():
            try:
                with ws_client.connect(self._url) as ws:
                    # Wait for connection message
                    msg = json.loads(ws.recv())
                    if isinstance(msg, list) and msg and msg[0].get("status") == "connected":
                        logger.info("Polygon WS connected to %s", self._url)

                    # Auth
                    ws.send(json.dumps({"action": "auth", "params": self._api_key}))
                    auth_resp = json.loads(ws.recv())
                    if isinstance(auth_resp, list) and auth_resp:
                        status = auth_resp[0].get("status")
                        if status == "auth_failed":
                            msg_text = auth_resp[0].get("message", "auth failed")
                            logger.error("Polygon auth failed: %s", msg_text)
                            if "not allowed" in msg_text.lower() or "not authorized" in msg_text.lower():
                                logger.error(
                                    "Polygon free tier does not support real-time WS. "
                                    "Upgrade to a paid plan for live streaming."
                                )
                                return  # Don't retry — plan limitation, not transient
                            break
                        elif status != "auth_success":
                            logger.warning("Polygon unexpected auth status: %s", auth_resp)

                    # Subscribe to trades + quotes
                    trade_params = ",".join(
                        f"XT.{self._format_symbol(s)}" for s in self._symbols
                    )
                    quote_params = ",".join(
                        f"XQ.{self._format_symbol(s)}" for s in self._symbols
                    )
                    ws.send(json.dumps({
                        "action": "subscribe",
                        "params": f"{trade_params},{quote_params}",
                    }))

                    backoff = self._BACKOFF_INITIAL_S
                    # Read loop
                    while not self._stop.is_set():
                        try:
                            raw = ws.recv(timeout=5.0)
                        except TimeoutError:
                            continue
                        try:
                            messages = json.loads(raw)
                        except (ValueError, TypeError):
                            continue
                        if not isinstance(messages, list):
                            messages = [messages]
                        for m in messages:
                            self._dispatch(m)

            except Exception:
                if self._stop.is_set():
                    break
                logger.exception("Polygon stream error; reconnecting in %.1fs", backoff)
                if self._stop.wait(backoff):
                    break
                backoff = min(backoff * 2, self._BACKOFF_MAX_S)

    def _dispatch(self, msg: dict) -> None:
        ev = msg.get("ev")
        if ev == "XT" or ev == "T":  # Trade (crypto uses XT, stocks use T)
            try:
                ts_ms = msg.get("t") or msg.get("timestamp")
                if isinstance(ts_ms, (int, float)):
                    ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
                else:
                    ts = datetime.now(timezone.utc)
                sym = self._normalize_symbol(msg.get("pair") or msg.get("sym") or "")
                self._on_trade({
                    "symbol": sym,
                    "timestamp": ts,
                    "price": float(msg.get("p", 0.0)),
                    "size": float(msg.get("s", 0.0)),
                })
            except Exception:
                logger.exception("Polygon trade dispatch error")
        elif ev == "XQ" or ev == "Q":  # Quote
            try:
                ts_ms = msg.get("t") or msg.get("timestamp")
                if isinstance(ts_ms, (int, float)):
                    ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
                else:
                    ts = datetime.now(timezone.utc)
                sym = self._normalize_symbol(msg.get("pair") or msg.get("sym") or "")
                self._on_quote({
                    "symbol": sym,
                    "timestamp": ts,
                    "bid": float(msg.get("bp") or msg.get("p", 0.0)),
                    "ask": float(msg.get("ap") or msg.get("P", 0.0)),
                    "bid_size": float(msg.get("bs") or msg.get("s", 0.0)),
                    "ask_size": float(msg.get("as") or msg.get("S", 0.0)),
                })
            except Exception:
                logger.exception("Polygon quote dispatch error")

    def close(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=3.0)
