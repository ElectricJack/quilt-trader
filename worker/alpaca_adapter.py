import logging
from typing import Optional

from worker.broker_adapter import BrokerAdapter, OrderResult

logger = logging.getLogger(__name__)


class AlpacaAdapter(BrokerAdapter):
    """Broker adapter for Alpaca Markets using alpaca-py SDK."""

    def __init__(self, api_key: str, secret_key: str, paper: bool = True) -> None:
        self._api_key = api_key
        self._secret_key = secret_key
        self._paper = paper
        self._trading_client = None
        self._data_client = None

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
