import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from worker.broker_adapter import (
    BrokerAdapter,
    BrokerTransaction,
    MarketDataStreamHandle,
    MultilegLegResult,
    MultilegOrderResult,
    OptionChainSnapshot,
    OptionContract,
    OrderResult,
)

logger = logging.getLogger(__name__)

_LIVE_BASE = "https://api.tradier.com/v1"
_SANDBOX_BASE = "https://sandbox.tradier.com/v1"


class TradierAdapter(BrokerAdapter):
    """Broker adapter for Tradier via REST API."""

    def __init__(
        self,
        access_token: str,
        account_id: str,
        sandbox: bool = True,
        paper: Optional[bool] = None,
    ) -> None:
        # `paper` is an alias for `sandbox` for parity with other adapters.
        if paper is not None:
            sandbox = paper
        self._access_token = access_token
        self._account_id = account_id
        self._sandbox = sandbox
        self._base_url = _SANDBOX_BASE if sandbox else _LIVE_BASE
        self._client: Optional[httpx.Client] = None

    def _ensure_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self._base_url,
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Accept": "application/json",
                },
                timeout=15.0,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def get_positions(self) -> dict[str, dict]:
        client = self._ensure_client()
        resp = client.get(f"/accounts/{self._account_id}/positions")
        resp.raise_for_status()
        payload = resp.json() or {}
        positions = payload.get("positions") or {}
        if positions in ("null", None):
            return {}
        items = positions.get("position") if isinstance(positions, dict) else None
        if items is None:
            return {}
        if isinstance(items, dict):
            items = [items]

        result: dict[str, dict] = {}
        for raw in items:
            symbol = raw.get("symbol")
            if not symbol:
                continue
            qty = float(raw.get("quantity", 0.0))
            cost_basis = float(raw.get("cost_basis", 0.0))
            avg_price = (cost_basis / qty) if qty else 0.0
            result[symbol] = {
                "symbol": symbol,
                "quantity": qty,
                "side": "long" if qty >= 0 else "short",
                "avg_price": avg_price,
                "current_price": 0.0,
                "unrealized_pnl": 0.0,
                "market_value": cost_basis,
            }
        return result

    def get_account_info(self) -> dict:
        client = self._ensure_client()
        resp = client.get(f"/accounts/{self._account_id}/balances")
        resp.raise_for_status()
        payload = resp.json() or {}
        balances = payload.get("balances") or {}
        total_equity = float(balances.get("total_equity", 0.0))
        total_cash = float(balances.get("total_cash", 0.0))
        # Tradier exposes account-type-specific buying power; pick a single field.
        bp = (
            balances.get("margin", {}).get("stock_buying_power")
            or balances.get("cash", {}).get("cash_available")
            or balances.get("pdt", {}).get("stock_buying_power")
            or 0.0
        )
        return {
            "cash": total_cash,
            "portfolio_value": total_equity,
            "buying_power": float(bp),
            "equity": total_equity,
            "currency": "USD",
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
        """Submit an equity order via Tradier.

        ``asset_type`` is accepted for signature consistency but unused —
        Tradier does not trade crypto so no TIF branching is needed.
        """
        client = self._ensure_client()

        side_lower = side.lower()
        if side_lower not in ("buy", "sell"):
            raise ValueError(f"Unsupported side: {side}")
        ot = order_type.lower()
        if ot not in ("market", "limit", "stop", "stop_limit"):
            raise ValueError(f"Unsupported order_type: {order_type}")

        body: dict = {
            "class": "equity",
            "symbol": symbol.upper(),
            "side": side_lower,
            "quantity": str(int(quantity)) if float(quantity).is_integer() else str(quantity),
            "type": ot,
            "duration": "day",
        }
        if ot in ("limit", "stop_limit") and limit_price is not None:
            body["price"] = str(limit_price)
        if ot in ("stop", "stop_limit") and stop_price is not None:
            body["stop"] = str(stop_price)

        resp = client.post(
            f"/accounts/{self._account_id}/orders",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        order = payload.get("order") or {}
        broker_order_id = str(order.get("id", "")) if order else ""
        # Tradier does not return fill price synchronously; leave at 0.0.
        return OrderResult(
            symbol=symbol,
            side=side,
            quantity=quantity,
            order_type=order_type,
            filled_price=0.0,
            fees=0.0,
            broker_order_id=broker_order_id or None,
        )

    # ---- Multi-leg orders (Spec A) ----
    def supports_multileg_orders(self, legs):
        if len(legs) < 2:
            return False
        if not all(l.asset_type == "options" for l in legs):
            return False
        return len({l.symbol for l in legs}) == 1

    def compose_symbol(self, leg):
        if leg.asset_type != "options":
            return leg.symbol
        if not (leg.expiry and leg.strike is not None and leg.right):
            raise ValueError(f"Options leg missing expiry/strike/right: {leg}")
        y, m, d = leg.expiry.split("-")
        right_ch = "C" if leg.right == "call" else "P"
        strike_int = round(leg.strike * 1000)
        return f"{leg.symbol}{y[2:]}{m}{d}{right_ch}{strike_int:08d}"

    def submit_multileg_order(self, legs, order_type, limit_price):
        import requests
        url = f"{self._base_url}/accounts/{self._account_id}/orders"
        # Tradier's "type" for net price: debit if net cost > 0, credit if < 0.
        net_sign = sum(1 if l.side == "buy" else -1 for l in legs)
        tradier_type = "debit" if net_sign >= 0 else "credit"
        data = {
            "class": "multileg",
            "symbol": legs[0].symbol,
            "type": tradier_type if order_type == "limit" else "market",
            "duration": "day",
        }
        if order_type == "limit":
            if limit_price is None:
                raise ValueError("limit_price required for limit order")
            data["price"] = f"{limit_price:.2f}"
        for i, leg in enumerate(legs):
            data[f"option_symbol[{i}]"] = self.compose_symbol(leg)
            side_map = {("buy",): "buy_to_open", ("sell",): "sell_to_open"}
            data[f"side[{i}]"] = side_map[(leg.side,)]
            data[f"quantity[{i}]"] = str(int(leg.quantity))
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
        }
        resp = requests.post(url, data=data, headers=headers, timeout=15)
        resp.raise_for_status()
        body = resp.json().get("order", {})
        order_id = str(body.get("id", ""))
        # Tradier returns one parent ID; per-leg fills appear later via order-status polling.
        leg_results = [
            MultilegLegResult(
                index=i,
                status="pending",
                broker_order_id=order_id,
            )
            for i in range(len(legs))
        ]
        return MultilegOrderResult(
            broker_order_id=order_id,
            legs=leg_results,
            atomic=True,
        )

    # ---- Options chain (Spec C) ----
    def list_option_expiries(self, underlying):
        from datetime import date
        import requests
        url = f"{self._base_url}/markets/options/expirations"
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
        }
        resp = requests.get(url, params={"symbol": underlying}, headers=headers, timeout=10)
        resp.raise_for_status()
        body = resp.json().get("expirations") or {}
        raw_dates = body.get("date") or []
        if isinstance(raw_dates, str):
            raw_dates = [raw_dates]
        return sorted(date.fromisoformat(d) for d in raw_dates)

    def get_option_chain(self, underlying, expiry):
        import requests
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
        }
        # Spot
        spot_resp = requests.get(
            f"{self._base_url}/markets/quotes",
            params={"symbols": underlying}, headers=headers, timeout=10,
        )
        spot_resp.raise_for_status()
        spot = float(spot_resp.json()["quotes"]["quote"]["last"])
        # Chain
        chain_resp = requests.get(
            f"{self._base_url}/markets/options/chains",
            params={"symbol": underlying, "expiration": expiry.isoformat(),
                    "greeks": "true"},
            headers=headers, timeout=15,
        )
        chain_resp.raise_for_status()
        opts = chain_resp.json().get("options") or {}
        raw_opts = opts.get("option") or []
        if isinstance(raw_opts, dict):
            raw_opts = [raw_opts]
        contracts = []
        for o in raw_opts:
            g = o.get("greeks") or {}
            contracts.append(OptionContract(
                strike=float(o["strike"]),
                right="call" if o.get("option_type") == "call" else "put",
                occ_symbol=o["symbol"],
                bid=o.get("bid"), ask=o.get("ask"), last=o.get("last"),
                iv=g.get("mid_iv"),
                delta=g.get("delta"), gamma=g.get("gamma"),
                theta=g.get("theta"), vega=g.get("vega"),
                open_interest=o.get("open_interest"),
                volume=o.get("volume"),
            ))
        contracts.sort(key=lambda c: (c.strike, c.right))
        return OptionChainSnapshot(
            underlying=underlying, spot=spot, expiry=expiry,
            contracts=contracts, as_of=None,
        )

    def get_latest_prices(self, symbols: list[str]) -> dict[str, float]:
        if not symbols:
            return {}
        client = self._ensure_client()
        try:
            resp = client.get("/markets/quotes", params={"symbols": ",".join(symbols)})
            resp.raise_for_status()
            quotes = resp.json().get("quotes", {}).get("quote")
            if quotes is None:
                return {}
            if isinstance(quotes, dict):
                quotes = [quotes]
            return {
                q["symbol"]: float(q["last"])
                for q in quotes
                if q.get("symbol") and q.get("last") is not None
            }
        except Exception:
            logger.warning("Failed to fetch Tradier quotes for %s", symbols, exc_info=True)
            return {}

    def get_transactions(self, since: datetime) -> list[BrokerTransaction]:
        """Window the date range into 30-day slices since Tradier caps at limit=500 per call."""
        client = self._ensure_client()
        start_date = since.astimezone(timezone.utc).date()
        end_date = datetime.now(timezone.utc).date()
        out: list[BrokerTransaction] = []
        window = timedelta(days=30)
        cursor = start_date
        while cursor <= end_date:
            window_end = min(cursor + window - timedelta(days=1), end_date)
            resp = client.get(
                f"/accounts/{self._account_id}/history",
                params={
                    "start": cursor.isoformat(),
                    "end": window_end.isoformat(),
                    "limit": 500,
                },
            )
            resp.raise_for_status()
            payload = resp.json() or {}
            history = payload.get("history") or {}
            if history not in ("null", None):
                events = history.get("event") if isinstance(history, dict) else None
                if events is not None:
                    if isinstance(events, dict):
                        events = [events]
                    for raw in events:
                        txn = _map_tradier_event(raw)
                        if txn is not None:
                            out.append(txn)
            cursor = window_end + timedelta(days=1)
        return out


    # ---- Live market-data stream (Spec B) ----
    def start_market_data_stream(
        self,
        symbols: list[str],
        on_trade,
        on_quote,
        asset_class: str = "equities",
    ) -> "MarketDataStreamHandle":
        if asset_class == "crypto":
            raise ValueError("Tradier does not support crypto streaming")
        # Note: streaming uses the production base URL even from sandbox keys.
        stream_base = _LIVE_BASE
        return _TradierStreamHandle(
            stream_base=stream_base,
            access_token=self._access_token,
            symbols=symbols,
            on_trade=on_trade,
            on_quote=on_quote,
        )


class _TradierStreamHandle(MarketDataStreamHandle):
    """Long-poll chunked HTTP stream of Tradier market events.

    The stream is a chunked HTTP response that Tradier may drop at any
    time (idle timeout, server restart, expired session). The thread
    reconnects with exponential backoff so we don't end up with the
    advertised tick rate sitting on a dead socket — which previously
    led to bars stopping at the moment of disconnect with no warning.
    """

    _BACKOFF_INITIAL_S = 1.0
    _BACKOFF_MAX_S = 30.0

    def __init__(
        self,
        stream_base: str,
        access_token: str,
        symbols: list[str],
        on_trade,
        on_quote,
    ) -> None:
        self._stream_base = stream_base
        self._access_token = access_token
        self._symbols = symbols
        self._on_trade = on_trade
        self._on_quote = on_quote
        self._stop = threading.Event()
        self._response: Optional[object] = None
        self._thread = threading.Thread(
            target=self._run, name="tradier-stream", daemon=True
        )
        self._thread.start()

    def _obtain_session(self) -> tuple[str, str]:
        """POST to get a fresh session id + stream url. Short-lived so we
        re-obtain on every (re)connect."""
        import requests

        resp = requests.post(
            f"{self._stream_base}/markets/events/session",
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Accept": "application/json",
            },
            timeout=10,
        )
        resp.raise_for_status()
        sess = resp.json().get("stream", {})
        session_id = sess.get("sessionid")
        if not session_id:
            raise RuntimeError("Tradier market events session: no sessionid returned")
        url = sess.get("url") or f"{self._stream_base}/markets/events"
        return session_id, url

    def _run(self) -> None:
        import requests

        backoff = self._BACKOFF_INITIAL_S
        while not self._stop.is_set():
            try:
                session_id, url = self._obtain_session()
                params = {
                    "sessionid": session_id,
                    "symbols": ",".join(self._symbols),
                    "filter": "trade,quote",
                    "linebreak": "true",
                }
                with requests.get(
                    url,
                    params=params,
                    headers={
                        "Authorization": f"Bearer {self._access_token}",
                        "Accept": "application/json",
                    },
                    stream=True,
                    timeout=None,
                ) as resp:
                    self._response = resp
                    resp.raise_for_status()
                    # Successfully connected — reset backoff for the next failure.
                    backoff = self._BACKOFF_INITIAL_S
                    for raw in resp.iter_lines(decode_unicode=True):
                        if self._stop.is_set():
                            break
                        if not raw:
                            continue
                        try:
                            msg = json.loads(raw)
                        except (ValueError, TypeError):
                            continue
                        self._dispatch(msg)
                # Reached end of iter_lines — server closed the stream cleanly.
                # Fall through to reconnect.
                if not self._stop.is_set():
                    logger.warning("tradier stream ended; reconnecting")
            except Exception:  # noqa: BLE001
                if self._stop.is_set():
                    break
                logger.exception(
                    "tradier stream error; reconnecting in %.1fs", backoff
                )
                if self._stop.wait(backoff):
                    break
                backoff = min(backoff * 2, self._BACKOFF_MAX_S)

    def _dispatch(self, msg: dict) -> None:
        kind = msg.get("type")
        ts_raw = msg.get("date") or msg.get("timestamp")
        try:
            if isinstance(ts_raw, str):
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            elif isinstance(ts_raw, (int, float)):
                ts = datetime.fromtimestamp(float(ts_raw) / 1000.0, tz=timezone.utc)
            else:
                ts = datetime.now(timezone.utc)
        except (ValueError, TypeError):
            ts = datetime.now(timezone.utc)

        if kind == "trade":
            try:
                self._on_trade({
                    "symbol": msg.get("symbol"),
                    "timestamp": ts,
                    "price": float(msg.get("price") or 0.0),
                    "size": float(msg.get("size") or 0.0),
                })
            except Exception:  # noqa: BLE001
                logger.exception("tradier trade handler error")
        elif kind == "quote":
            try:
                self._on_quote({
                    "symbol": msg.get("symbol"),
                    "timestamp": ts,
                    "bid": float(msg.get("bid") or 0.0),
                    "ask": float(msg.get("ask") or 0.0),
                    "bid_size": float(msg.get("bidsz") or 0.0),
                    "ask_size": float(msg.get("asksz") or 0.0),
                })
            except Exception:  # noqa: BLE001
                logger.exception("tradier quote handler error")

    def close(self) -> None:
        self._stop.set()
        try:
            resp = self._response
            if resp is not None:
                resp.close()
        except Exception:  # noqa: BLE001
            pass
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)


def _parse_tradier_dt(raw: dict) -> datetime:
    ts = raw.get("date")
    if not ts:
        return datetime.now(timezone.utc)
    # Tradier returns "2026-05-13T14:30:00.000Z" or similar.
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _tradier_synthetic_id(raw: dict, event_type: str) -> str:
    """Tradier history events don't always have an id; synthesize one from fields."""
    parts = [
        event_type,
        raw.get("date") or "",
        str(raw.get("amount") or ""),
        str((raw.get("trade") or {}).get("symbol") or raw.get("symbol") or ""),
        str((raw.get("trade") or {}).get("quantity") or ""),
        str((raw.get("trade") or {}).get("price") or ""),
    ]
    return "tradier:" + ":".join(parts)


def _map_tradier_event(raw: dict) -> Optional[BrokerTransaction]:
    event_type = (raw.get("type") or "").lower()
    ts = _parse_tradier_dt(raw)
    amount = float(raw.get("amount") or 0.0)
    txn_id = _tradier_synthetic_id(raw, event_type)
    description = raw.get("description")

    if event_type == "option" and "trade" not in raw:
        # Option expiration event — close the position at $0
        opt = raw.get("option") or {}
        symbol = opt.get("symbol")
        if symbol:
            return BrokerTransaction(
                broker_id=txn_id,
                type="fill",
                timestamp=ts,
                symbol=symbol,
                side="sell",
                quantity=0,
                price=0.0,
                amount=0.0,
                description=f"Option expiration: {symbol}",
                raw=raw,
            )

    if event_type in ("trade", "option"):
        trade = raw.get("trade") or {}
        qty = float(trade.get("quantity") or 0.0)
        price = float(trade.get("price") or 0.0)
        side_raw = (trade.get("trade_type") or trade.get("side") or "").lower()
        # Tradier uses values like "buy", "sell", "sell_short", "buy_to_cover".
        if "buy" in side_raw:
            side = "buy"
        elif "sell" in side_raw:
            side = "sell"
        else:
            side = side_raw or "buy"
        return BrokerTransaction(
            broker_id=txn_id,
            type="fill",
            timestamp=ts,
            symbol=trade.get("symbol"),
            side=side,
            quantity=qty,
            price=price,
            amount=amount,
            description=description,
            raw=raw,
        )

    if event_type == "dividend":
        return BrokerTransaction(
            broker_id=txn_id,
            type="dividend",
            timestamp=ts,
            amount=amount,
            symbol=(raw.get("dividend") or {}).get("symbol"),
            description=description,
            raw=raw,
        )
    if event_type == "interest":
        return BrokerTransaction(
            broker_id=txn_id,
            type="interest",
            timestamp=ts,
            amount=amount,
            description=description,
            raw=raw,
        )
    if event_type in ("ach", "wire", "journal", "check"):
        return BrokerTransaction(
            broker_id=txn_id,
            type="deposit" if amount >= 0 else "withdrawal",
            timestamp=ts,
            amount=amount,
            description=description,
            raw=raw,
        )
    return None
