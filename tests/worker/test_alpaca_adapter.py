import pytest
from unittest.mock import MagicMock, patch
from worker.alpaca_adapter import AlpacaAdapter
from worker.broker_adapter import OrderResult


class TestAlpacaAdapter:
    def test_init(self):
        adapter = AlpacaAdapter(api_key="test", secret_key="test", paper=True)
        assert adapter._paper is True
        assert adapter._trading_client is None

    def test_init_defaults_to_paper(self):
        adapter = AlpacaAdapter(api_key="k", secret_key="s")
        assert adapter._paper is True

    def test_get_positions_lazy_init(self):
        adapter = AlpacaAdapter(api_key="test", secret_key="test")
        mock_client = MagicMock()
        mock_client.get_all_positions.return_value = []
        with patch.object(adapter, "_ensure_clients"):
            adapter._trading_client = mock_client
            result = adapter.get_positions()
            assert result == {}

    def test_get_positions_maps_fields(self):
        adapter = AlpacaAdapter(api_key="test", secret_key="test")
        mock_client = MagicMock()
        mock_pos = MagicMock()
        mock_pos.symbol = "AAPL"
        mock_pos.qty = "10"
        mock_pos.side.value = "long"
        mock_pos.avg_entry_price = "150.00"
        mock_pos.current_price = "155.00"
        mock_pos.unrealized_pl = "50.00"
        mock_pos.market_value = "1550.00"
        mock_client.get_all_positions.return_value = [mock_pos]
        with patch.object(adapter, "_ensure_clients"):
            adapter._trading_client = mock_client
            result = adapter.get_positions()
            assert "AAPL" in result
            pos = result["AAPL"]
            assert pos["symbol"] == "AAPL"
            assert pos["quantity"] == 10.0
            assert pos["side"] == "long"
            assert pos["avg_price"] == 150.0
            assert pos["current_price"] == 155.0
            assert pos["unrealized_pnl"] == 50.0
            assert pos["market_value"] == 1550.0

    def test_get_account_info(self):
        adapter = AlpacaAdapter(api_key="test", secret_key="test")
        mock_client = MagicMock()
        mock_account = MagicMock()
        mock_account.cash = "50000.00"
        mock_account.portfolio_value = "75000.00"
        mock_account.buying_power = "100000.00"
        mock_account.equity = "75000.00"
        mock_account.currency = "USD"
        mock_client.get_account.return_value = mock_account
        with patch.object(adapter, "_ensure_clients"):
            adapter._trading_client = mock_client
            info = adapter.get_account_info()
            assert info["cash"] == 50000.0
            assert info["portfolio_value"] == 75000.0
            assert info["buying_power"] == 100000.0
            assert info["equity"] == 75000.0
            assert info["currency"] == "USD"

    def test_submit_market_order(self):
        adapter = AlpacaAdapter(api_key="test", secret_key="test")
        mock_client = MagicMock()
        mock_order = MagicMock()
        mock_order.filled_avg_price = "152.50"
        mock_order.id = "order-uuid-1"
        mock_client.submit_order.return_value = mock_order

        mock_market_request_cls = MagicMock()
        mock_market_request_cls.return_value = MagicMock()

        with patch.object(adapter, "_ensure_clients"), \
             patch("worker.alpaca_adapter.AlpacaAdapter.submit_order",
                   wraps=adapter.submit_order):
            adapter._trading_client = mock_client
            # Patch the imports inside submit_order
            import sys
            alpaca_trading = MagicMock()
            alpaca_trading.requests.MarketOrderRequest = mock_market_request_cls
            alpaca_trading.requests.LimitOrderRequest = MagicMock()
            alpaca_trading.enums.OrderSide.BUY = "buy"
            alpaca_trading.enums.OrderSide.SELL = "sell"
            alpaca_trading.enums.TimeInForce.DAY = "day"

            with patch.dict(sys.modules, {
                "alpaca": MagicMock(),
                "alpaca.trading": alpaca_trading,
                "alpaca.trading.client": MagicMock(),
                "alpaca.trading.requests": alpaca_trading.requests,
                "alpaca.trading.enums": alpaca_trading.enums,
            }):
                result = adapter.submit_order(
                    symbol="AAPL", side="buy", quantity=10.0, order_type="market"
                )
                assert isinstance(result, OrderResult)
                assert result.symbol == "AAPL"
                assert result.side == "buy"
                assert result.quantity == 10.0
                assert result.order_type == "market"
                assert result.filled_price == 152.5
                assert result.broker_order_id == "order-uuid-1"

    def test_ensure_clients_raises_import_error(self):
        adapter = AlpacaAdapter(api_key="test", secret_key="test")
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "alpaca.trading.client":
                raise ImportError("alpaca-py not installed")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            with pytest.raises(ImportError, match="alpaca-py is required"):
                adapter._ensure_clients()

    def test_submit_order_zero_filled_price_when_none(self):
        adapter = AlpacaAdapter(api_key="test", secret_key="test")
        mock_client = MagicMock()
        mock_order = MagicMock()
        mock_order.filled_avg_price = None
        mock_order.id = "order-uuid-2"
        mock_client.submit_order.return_value = mock_order

        import sys
        alpaca_trading = MagicMock()
        alpaca_trading.requests.MarketOrderRequest = MagicMock(return_value=MagicMock())
        alpaca_trading.enums.OrderSide.BUY = "buy"
        alpaca_trading.enums.OrderSide.SELL = "sell"
        alpaca_trading.enums.TimeInForce.DAY = "day"

        with patch.object(adapter, "_ensure_clients"), \
             patch.dict(sys.modules, {
                 "alpaca": MagicMock(),
                 "alpaca.trading": alpaca_trading,
                 "alpaca.trading.client": MagicMock(),
                 "alpaca.trading.requests": alpaca_trading.requests,
                 "alpaca.trading.enums": alpaca_trading.enums,
             }):
            adapter._trading_client = mock_client
            result = adapter.submit_order(
                symbol="TSLA", side="sell", quantity=5.0, order_type="market"
            )
            assert result.filled_price == 0.0
