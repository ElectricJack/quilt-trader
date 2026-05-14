import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from worker.broker_adapter import BrokerAdapter, BrokerTransaction, OrderResult

logger = logging.getLogger(__name__)

_LIVE_BASE = "https://api.tradier.com/v1"
_SANDBOX_BASE = "https://sandbox.tradier.com/v1"


class TradierAdapter(BrokerAdapter):
    """Broker adapter for Tradier via REST API."""

    def __init__(self, access_token: str, account_id: str, sandbox: bool = True) -> None:
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
    ) -> OrderResult:
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
