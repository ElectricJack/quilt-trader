import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from worker.broker_adapter import BrokerAdapter, BrokerTransaction, OrderResult

logger = logging.getLogger(__name__)

_PAPER_BASE = "https://paper-api.alpaca.markets"
_LIVE_BASE = "https://api.alpaca.markets"


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
    ) -> OrderResult:
        self._ensure_clients()
        from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        order_side = OrderSide.BUY if side.lower() == "buy" else OrderSide.SELL

        if order_type.lower() == "limit" and limit_price is not None:
            request = LimitOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=order_side,
                time_in_force=TimeInForce.DAY,
                limit_price=limit_price,
            )
        else:
            request = MarketOrderRequest(
                symbol=symbol,
                qty=quantity,
                side=order_side,
                time_in_force=TimeInForce.DAY,
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
