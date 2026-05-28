import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx

from worker.broker_adapter import (
    BrokerAdapter,
    BrokerTransaction,
    MarketDataStreamHandle,
    MultilegLegResult,
    MultilegLegSpec,
    MultilegOrderResult,
    OptionChainSnapshot,
    OptionContract,
    OrderResult,
)


@dataclass
class _NullGreeks:
    """Fallback when an option snapshot lacks a greeks payload."""
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None

logger = logging.getLogger(__name__)

_PAPER_BASE = "https://paper-api.alpaca.markets"
_LIVE_BASE = "https://api.alpaca.markets"


def _map_asset_class(alpaca_class) -> str:
    """Map Alpaca's asset_class enum/string to Quilt's convention."""
    s = alpaca_class.value if hasattr(alpaca_class, "value") else str(alpaca_class)
    s = s.lower()
    if s == "crypto":
        return "crypto"
    if s == "us_option":
        return "options"
    return "equities"  # default us_equity and anything unknown


class AlpacaAdapter(BrokerAdapter):
    """Broker adapter for Alpaca Markets.

    Trading operations (positions, account info, order submission) use the alpaca-py SDK.
    The activities/history endpoint is called via httpx to avoid coupling test mocks to SDK shape.
    """

    def __init__(self, api_key: str, secret_key: str, paper: bool = True) -> None:
        self._api_key = api_key
        self._secret_key = secret_key
        self._paper = paper
        self._base_url = _PAPER_BASE if paper else _LIVE_BASE
        self._trading_client = None
        self._data_client = None
        self._http: Optional[httpx.Client] = None

    def _ensure_clients(self) -> None:
        if self._trading_client is not None:
            return
        try:
            from alpaca.trading.client import TradingClient
            self._trading_client = TradingClient(
                self._api_key, self._secret_key, paper=self._paper
            )
        except ImportError:
            raise ImportError(
                "alpaca-py is required for AlpacaAdapter. "
                "Install with: pip install alpaca-py"
            )
        if self._data_client is None:
            self._data_client = _AlpacaDataClient(
                self._api_key, self._secret_key, self._trading_client
            )

    def _ensure_http(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(
                base_url=self._base_url,
                headers={
                    "APCA-API-KEY-ID": self._api_key,
                    "APCA-API-SECRET-KEY": self._secret_key,
                    "Accept": "application/json",
                },
                timeout=20.0,
            )
        return self._http

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    def get_positions(self) -> dict[str, dict]:
        self._ensure_clients()
        positions = self._trading_client.get_all_positions()
        result = {}
        for pos in positions:
            result[pos.symbol] = {
                "symbol": pos.symbol,
                "quantity": float(pos.qty),
                "side": pos.side.value if hasattr(pos.side, "value") else str(pos.side),
                "asset_class": _map_asset_class(pos.asset_class),
                "avg_price": float(pos.avg_entry_price),
                "current_price": float(pos.current_price),
                "unrealized_pnl": float(pos.unrealized_pl),
                "market_value": float(pos.market_value),
            }
        return result

    def get_account_info(self) -> dict:
        self._ensure_clients()
        account = self._trading_client.get_account()
        return {
            "cash": float(account.cash),
            "portfolio_value": float(account.portfolio_value),
            "buying_power": float(account.buying_power),
            "equity": float(account.equity),
            "currency": account.currency,
        }

    def submit_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str,
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        asset_type: Optional[str] = None,
    ) -> OrderResult:
        """Submit a single-leg order.

        ``asset_type`` is used to select the correct TimeInForce: Alpaca requires
        ``GTC`` for crypto and ``DAY`` for equities/options.
        """
        self._ensure_clients()
        from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        from coordinator.services.asset_services import get_default_registry
        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL
        tif_str = get_default_registry().time_in_force(symbol)
        tif = TimeInForce.GTC if tif_str == "GTC" else TimeInForce.DAY

        if order_type.lower() == "limit" and limit_price is not None:
            request = LimitOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=order_side,
                time_in_force=tif,
                limit_price=limit_price,
            )
        else:
            request = MarketOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=order_side,
                time_in_force=tif,
            )

        order = self._trading_client.submit_order(request)
        filled_price = float(order.filled_avg_price) if order.filled_avg_price else 0.0

        return OrderResult(
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            filled_price=filled_price,
            fees=0.0,
            broker_order_id=str(order.id),
        )

    def get_transactions(self, since: datetime) -> list[BrokerTransaction]:
        http = self._ensure_http()
        params = {
            "after": since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "page_size": "100",
            "direction": "asc",
        }
        out: list[BrokerTransaction] = []
        # Paginate via page_token.
        while True:
            resp = http.get("/v2/account/activities", params=params)
            resp.raise_for_status()
            items = resp.json() or []
            if not items:
                break
            for raw in items:
                txn = _map_alpaca_activity(raw)
                if txn is not None:
                    out.append(txn)
            if len(items) < 100:
                break
            last_id = items[-1].get("id")
            if not last_id:
                break
            params["page_token"] = last_id
        return out

    def get_latest_prices(self, symbols: list[str]) -> dict[str, float]:
        self._ensure_clients()
        from alpaca.data.requests import StockLatestTradeRequest
        if not symbols:
            return {}
        try:
            resp = self._data_client.get_stock_latest_trade(
                StockLatestTradeRequest(symbol_or_symbols=symbols)
            )
            return {sym: float(trade.price) for sym, trade in resp.items()}
        except Exception:
            logger.warning("Failed to fetch latest prices for %s", symbols, exc_info=True)
            return {}

    # ---- Multi-leg orders (Spec A) ----
    def supports_multileg_orders(self, legs: list[MultilegLegSpec]) -> bool:
        if len(legs) < 2:
            return False
        from coordinator.services.asset_services import get_default_registry
        registry = get_default_registry()
        if not all(registry.get_service_by_type(l.asset_type).supports_multileg() for l in legs):
            return False
        underlyings = {l.symbol for l in legs}
        return len(underlyings) == 1

    def compose_symbol(self, leg: MultilegLegSpec) -> str:
        from coordinator.services.asset_services import get_default_registry
        return get_default_registry().compose_order_symbol(leg)

    def submit_multileg_order(
        self,
        legs: list[MultilegLegSpec],
        order_type: str,
        limit_price: Optional[float],
    ) -> MultilegOrderResult:
        from alpaca.trading.requests import (
            OptionLegRequest,
            LimitOrderRequest,
            MarketOrderRequest,
        )
        from alpaca.trading.enums import (
            OrderSide,
            OrderClass,
            PositionIntent,
            TimeInForce,
        )

        self._ensure_clients()
        req_legs = []
        for leg in legs:
            intent = getattr(leg, "position_intent", "open")
            req_legs.append(OptionLegRequest(
                symbol=self.compose_symbol(leg),
                side=OrderSide.BUY if leg.side == "buy" else OrderSide.SELL,
                ratio_qty=int(leg.quantity),
                position_intent=(
                    (PositionIntent.BUY_TO_CLOSE if leg.side == "buy" else PositionIntent.SELL_TO_CLOSE)
                    if intent == "close"
                    else (PositionIntent.BUY_TO_OPEN if leg.side == "buy" else PositionIntent.SELL_TO_OPEN)
                ),
            ))
        if order_type == "limit":
            if limit_price is None:
                raise ValueError("limit_price required for limit order")
            req = LimitOrderRequest(
                qty=1,  # ratio defined per leg
                order_class=OrderClass.MLEG,
                time_in_force=TimeInForce.DAY,
                limit_price=limit_price,
                legs=req_legs,
            )
        else:
            req = MarketOrderRequest(
                qty=1,
                order_class=OrderClass.MLEG,
                time_in_force=TimeInForce.DAY,
                legs=req_legs,
            )
        order = self._trading_client.submit_order(req)
        leg_results = []
        for i, lg in enumerate(getattr(order, "legs", []) or []):
            filled_raw = getattr(lg, "filled_avg_price", 0) or 0
            filled_price = float(filled_raw) if filled_raw else None
            leg_results.append(MultilegLegResult(
                index=i,
                status=str(getattr(lg, "status", "pending")).lower(),
                filled_price=filled_price,
                fees=None,  # Alpaca returns fees separately, omit for v1
                broker_order_id=str(getattr(lg, "id", "")) or None,
            ))
        return MultilegOrderResult(
            broker_order_id=str(order.id),
            legs=leg_results,
            atomic=True,
        )

    # ---- Options chain (Spec C) ----
    def list_option_expiries(self, underlying: str):
        """List all known expiration dates for ``underlying``.

        Alpaca's /v2/options/contracts paginates at 10000 rows per page.
        Walk all pages and also sweep multiple expiration_date windows so
        we surface the full expiry calendar (weeklies + monthlies +
        LEAPS), not just the soonest ones — Alpaca's default sort
        otherwise returns the nearest contracts first and may stop
        before reaching LEAPS.
        """
        from alpaca.trading.requests import GetOptionContractsRequest
        from datetime import date, timedelta
        self._ensure_clients()
        dates: set = set()
        # Sweep ~2 years out in 90-day windows. Each window paginates
        # independently. The union covers weeklies + monthlies + LEAPS.
        today = date.today()
        windows = [
            (today + timedelta(days=i), today + timedelta(days=i + 90))
            for i in range(0, 730, 90)
        ]
        for win_start, win_end in windows:
            page_token = None
            for _ in range(20):  # defensive page cap per window
                req = GetOptionContractsRequest(
                    underlying_symbols=[underlying],
                    expiration_date_gte=win_start,
                    expiration_date_lte=win_end,
                    limit=10000,
                    page_token=page_token,
                )
                resp = self._data_client.get_option_contracts(req)
                contracts = resp.option_contracts or []
                for c in contracts:
                    dates.add(c.expiration_date)
                page_token = getattr(resp, "next_page_token", None)
                if not page_token or not contracts:
                    break
        return sorted(dates)

    def get_option_chain(self, underlying: str, expiry) -> OptionChainSnapshot:
        from alpaca.data.requests import (
            OptionChainRequest,
            StockLatestTradeRequest,
        )
        self._ensure_clients()
        spot_resp = self._data_client.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=[underlying])
        )
        spot = float(spot_resp[underlying].price)

        chain_resp = self._data_client.get_option_chain(
            OptionChainRequest(underlying_symbol=underlying, expiration_date=expiry)
        )
        # alpaca-py returns a dict[occ_symbol, OptionsSnapshot] directly
        snapshots = chain_resp if isinstance(chain_resp, dict) else (chain_resp.snapshots or {})
        contracts = []
        for occ_sym, snap in snapshots.items():
            # Parse OCC: <UNDERLYING><YY><MM><DD><C|P><strike*1000 padded 8>
            tail = occ_sym[len(underlying):]
            right = "call" if tail[6] == "C" else "put"
            strike = int(tail[7:15]) / 1000
            q = getattr(snap, "latest_quote", None)
            t = getattr(snap, "latest_trade", None)
            g = getattr(snap, "greeks", None) or _NullGreeks()
            db = getattr(snap, "daily_bar", None)
            contracts.append(OptionContract(
                strike=strike, right=right, occ_symbol=occ_sym,
                bid=getattr(q, "bid_price", None) if q else None,
                ask=getattr(q, "ask_price", None) if q else None,
                last=getattr(t, "price", None) if t else None,
                iv=getattr(snap, "implied_volatility", None),
                delta=getattr(g, "delta", None),
                gamma=getattr(g, "gamma", None),
                theta=getattr(g, "theta", None),
                vega=getattr(g, "vega", None),
                open_interest=getattr(snap, "open_interest", None),
                volume=getattr(db, "volume", None) if db else None,
            ))
        contracts.sort(key=lambda c: (c.strike, c.right))
        as_of = max(
            (
                getattr(getattr(s, "latest_quote", None), "timestamp", None)
                for s in snapshots.values()
                if getattr(s, "latest_quote", None) is not None
            ),
            default=None,
        )
        return OptionChainSnapshot(
            underlying=underlying, spot=spot, expiry=expiry,
            contracts=contracts, as_of=as_of,
        )


    # ---- Live market-data stream (Spec B) ----
    def start_market_data_stream(
        self,
        symbols: list[str],
        on_trade,
        on_quote,
        asset_class: str = "equities",
    ) -> "MarketDataStreamHandle":
        from alpaca.data.live import StockDataStream, CryptoDataStream
        from coordinator.services.asset_services import get_default_registry

        # Resolve stream config via the registry — Alpaca crypto needs
        # BTC/USD-style symbols for streaming while order endpoints accept BTCUSD.
        registry = get_default_registry()
        first = symbols[0] if symbols else ""
        cfg = registry.stream_config(first, "alpaca") if first else None
        if cfg and cfg.symbol_transform == "crypto_slash":
            original_by_streamed = {
                registry.resolve_symbol(s, "alpaca_stream"): s for s in symbols
            }
            symbols = list(original_by_streamed.keys())
        else:
            original_by_streamed = {s: s for s in symbols}

        stream_cls = CryptoDataStream if (cfg and cfg.stream_class == "crypto") else StockDataStream
        stream = stream_cls(self._api_key, self._secret_key)

        async def _trade_handler(data):
            try:
                streamed_symbol = getattr(data, "symbol", None)
                tick = {
                    "symbol": original_by_streamed.get(streamed_symbol, streamed_symbol),
                    "timestamp": getattr(data, "timestamp", None) or datetime.now(timezone.utc),
                    "price": float(getattr(data, "price", 0.0) or 0.0),
                    "size": float(getattr(data, "size", 0.0) or 0.0),
                }
                on_trade(tick)
            except Exception:  # noqa: BLE001
                logger.exception("alpaca trade handler error")

        async def _quote_handler(data):
            try:
                streamed_symbol = getattr(data, "symbol", None)
                tick = {
                    "symbol": original_by_streamed.get(streamed_symbol, streamed_symbol),
                    "timestamp": getattr(data, "timestamp", None) or datetime.now(timezone.utc),
                    "bid": float(getattr(data, "bid_price", 0.0) or 0.0),
                    "ask": float(getattr(data, "ask_price", 0.0) or 0.0),
                    "bid_size": float(getattr(data, "bid_size", 0.0) or 0.0),
                    "ask_size": float(getattr(data, "ask_size", 0.0) or 0.0),
                }
                on_quote(tick)
            except Exception:  # noqa: BLE001
                logger.exception("alpaca quote handler error")

        stream.subscribe_trades(_trade_handler, *symbols)
        stream.subscribe_quotes(_quote_handler, *symbols)

        return _AlpacaStreamHandle(stream)


class _AlpacaStreamHandle(MarketDataStreamHandle):
    """Runs an alpaca-py ``StockDataStream`` in a daemon thread."""

    def __init__(self, stream) -> None:
        super().__init__()
        self._stream = stream
        self._closed_intentionally = False
        self._thread = threading.Thread(
            target=self._run, name="alpaca-stream", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        try:
            self._stream.run()
        except Exception:  # noqa: BLE001
            logger.exception("alpaca stream thread crashed")
        finally:
            # Thread exit on a stream that wasn't intentionally closed = disconnect.
            if not self._closed_intentionally:
                self._fire_on_disconnect()

    def close(self) -> None:
        self._closed_intentionally = True
        try:
            self._stream.stop()
        except Exception:  # noqa: BLE001
            logger.exception("alpaca stream stop() failed")
        # Best-effort thread join — alpaca-py's stop() is async and may not
        # cleanly shut down synchronously; we don't block forever.
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)


class _AlpacaDataClient:
    """Composite that exposes the option chain, option contracts metadata, and
    stock-latest-trade endpoints behind a single object. Lazy-initializes the
    three underlying alpaca-py clients on first use so unit tests can patch
    `_ensure_clients` and substitute a single mock for this whole composite."""

    def __init__(self, api_key: str, secret_key: str, trading_client) -> None:
        self._api_key = api_key
        self._secret_key = secret_key
        self._trading_client = trading_client
        self._stock_data = None
        self._option_data = None

    def _ensure_stock_data(self):
        if self._stock_data is None:
            from alpaca.data.historical import StockHistoricalDataClient
            self._stock_data = StockHistoricalDataClient(
                self._api_key, self._secret_key
            )
        return self._stock_data

    def _ensure_option_data(self):
        if self._option_data is None:
            from alpaca.data.historical.option import OptionHistoricalDataClient
            self._option_data = OptionHistoricalDataClient(
                self._api_key, self._secret_key
            )
        return self._option_data

    def get_option_contracts(self, request):
        return self._trading_client.get_option_contracts(request)

    def get_option_chain(self, request):
        return self._ensure_option_data().get_option_chain(request)

    def get_stock_latest_trade(self, request):
        return self._ensure_stock_data().get_stock_latest_trade(request)


_ALPACA_DEPOSIT_TYPES = {"CSD", "ACATC", "JNLC_IN"}
_ALPACA_WITHDRAWAL_TYPES = {"CSW", "ACATS"}


def _parse_alpaca_dt(raw: dict) -> datetime:
    ts = raw.get("transaction_time") or raw.get("date") or raw.get("created_at")
    if not ts:
        return datetime.now(timezone.utc)
    # Alpaca formats: "2026-05-13T14:30:00Z" or just "2026-05-13"
    if "T" in ts:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return datetime.fromisoformat(ts + "T00:00:00+00:00")


def _map_alpaca_activity(raw: dict) -> Optional[BrokerTransaction]:
    act = (raw.get("activity_type") or "").upper()
    txn_id = str(raw.get("id") or "")
    if not txn_id:
        return None
    ts = _parse_alpaca_dt(raw)

    if act == "FILL":
        side = (raw.get("side") or "").lower()
        qty = float(raw.get("qty") or 0.0)
        price = float(raw.get("price") or 0.0)
        return BrokerTransaction(
            broker_id=txn_id,
            type="fill",
            timestamp=ts,
            symbol=raw.get("symbol"),
            side=side,
            quantity=qty,
            price=price,
            amount=qty * price * (1 if side == "sell" else -1),
            description=raw.get("description"),
            raw=raw,
        )

    if act in ("DIV", "DIVNRA", "DIVTAX", "INT", "INTNRA"):
        amount = float(raw.get("net_amount") or 0.0)
        return BrokerTransaction(
            broker_id=txn_id,
            type="dividend" if act.startswith("DIV") else "interest",
            timestamp=ts,
            amount=amount,
            symbol=raw.get("symbol"),
            description=raw.get("description"),
            raw=raw,
        )

    if act in ("CSD", "ACATC"):
        return BrokerTransaction(
            broker_id=txn_id,
            type="deposit",
            timestamp=ts,
            amount=float(raw.get("net_amount") or 0.0),
            description=raw.get("description"),
            raw=raw,
        )
    if act in ("CSW", "ACATS"):
        return BrokerTransaction(
            broker_id=txn_id,
            type="withdrawal",
            timestamp=ts,
            amount=float(raw.get("net_amount") or 0.0),
            description=raw.get("description"),
            raw=raw,
        )
    if act == "JNLC":
        amount = float(raw.get("net_amount") or 0.0)
        return BrokerTransaction(
            broker_id=txn_id,
            type="deposit" if amount >= 0 else "withdrawal",
            timestamp=ts,
            amount=amount,
            description=raw.get("description"),
            raw=raw,
        )
    if act == "FEE":
        return BrokerTransaction(
            broker_id=txn_id,
            type="fee",
            timestamp=ts,
            amount=float(raw.get("net_amount") or 0.0),
            description=raw.get("description"),
            raw=raw,
        )
    # Unknown activity types are skipped (e.g. SPIN, SPLIT, MA).
    return None
