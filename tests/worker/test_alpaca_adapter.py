import pytest
from unittest.mock import MagicMock, patch
from worker.alpaca_adapter import AlpacaAdapter
from worker.broker_adapter import MultilegLegSpec, OrderResult


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

    def test_submit_order_uses_gtc_for_crypto(self):
        """Alpaca rejects crypto orders with DAY; adapter must use GTC."""
        adapter = AlpacaAdapter(api_key="test", secret_key="test")
        mock_client = MagicMock()
        mock_order = MagicMock()
        mock_order.filled_avg_price = "50000.00"
        mock_order.id = "order-crypto-1"
        mock_client.submit_order.return_value = mock_order

        import sys
        alpaca_trading = MagicMock()
        captured_requests = []

        def capture_market_request(**kwargs):
            captured_requests.append(kwargs)
            return MagicMock(**kwargs)

        alpaca_trading.requests.MarketOrderRequest = capture_market_request
        alpaca_trading.requests.LimitOrderRequest = MagicMock(return_value=MagicMock())
        alpaca_trading.enums.OrderSide.BUY = "buy"
        alpaca_trading.enums.OrderSide.SELL = "sell"
        alpaca_trading.enums.TimeInForce.DAY = "day"
        alpaca_trading.enums.TimeInForce.GTC = "gtc"

        with patch.object(adapter, "_ensure_clients"), \
             patch.dict(sys.modules, {
                 "alpaca": MagicMock(),
                 "alpaca.trading": alpaca_trading,
                 "alpaca.trading.client": MagicMock(),
                 "alpaca.trading.requests": alpaca_trading.requests,
                 "alpaca.trading.enums": alpaca_trading.enums,
             }):
            adapter._trading_client = mock_client
            adapter.submit_order(
                symbol="BTCUSD", side="sell", quantity=0.005,
                order_type="market", asset_type="crypto",
            )
        assert len(captured_requests) == 1
        assert captured_requests[0]["time_in_force"] == "gtc"

    def test_submit_order_uses_day_for_equities(self):
        """Equities must continue to use DAY time-in-force."""
        adapter = AlpacaAdapter(api_key="test", secret_key="test")
        mock_client = MagicMock()
        mock_order = MagicMock()
        mock_order.filled_avg_price = "500.00"
        mock_order.id = "order-eq-1"
        mock_client.submit_order.return_value = mock_order

        import sys
        alpaca_trading = MagicMock()
        captured_requests = []

        def capture_market_request(**kwargs):
            captured_requests.append(kwargs)
            return MagicMock(**kwargs)

        alpaca_trading.requests.MarketOrderRequest = capture_market_request
        alpaca_trading.requests.LimitOrderRequest = MagicMock(return_value=MagicMock())
        alpaca_trading.enums.OrderSide.BUY = "buy"
        alpaca_trading.enums.OrderSide.SELL = "sell"
        alpaca_trading.enums.TimeInForce.DAY = "day"
        alpaca_trading.enums.TimeInForce.GTC = "gtc"

        with patch.object(adapter, "_ensure_clients"), \
             patch.dict(sys.modules, {
                 "alpaca": MagicMock(),
                 "alpaca.trading": alpaca_trading,
                 "alpaca.trading.client": MagicMock(),
                 "alpaca.trading.requests": alpaca_trading.requests,
                 "alpaca.trading.enums": alpaca_trading.enums,
             }):
            adapter._trading_client = mock_client
            adapter.submit_order(
                symbol="SPY", side="sell", quantity=5.0,
                order_type="market", asset_type="equities",
            )
        assert len(captured_requests) == 1
        assert captured_requests[0]["time_in_force"] == "day"


# ---- Multi-leg orders + options chain (Work Unit A1) ----

def _opt_leg(side, right, strike, expiry="2026-06-20"):
    return MultilegLegSpec(symbol="SPY", asset_type="options", side=side,
                           quantity=1, expiry=expiry, strike=strike, right=right)


def test_supports_multileg_same_underlying_options():
    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    legs = [_opt_leg("buy", "call", 560), _opt_leg("sell", "call", 570)]
    assert adapter.supports_multileg_orders(legs) is True


def test_supports_multileg_false_for_mixed_underlyings():
    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    leg1 = _opt_leg("buy", "call", 560)
    leg2 = MultilegLegSpec(symbol="QQQ", asset_type="options", side="sell",
                           quantity=1, expiry="2026-06-20", strike=450, right="call")
    assert adapter.supports_multileg_orders([leg1, leg2]) is False


def test_supports_multileg_false_for_non_options():
    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    leg = MultilegLegSpec(symbol="SPY", asset_type="equities", side="buy", quantity=100)
    assert adapter.supports_multileg_orders([leg, leg]) is False


def test_compose_symbol_call_occ():
    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    leg = _opt_leg("buy", "call", 560.0, expiry="2026-06-20")
    assert adapter.compose_symbol(leg) == "SPY260620C00560000"


def test_compose_symbol_put_occ():
    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    leg = _opt_leg("sell", "put", 565.50, expiry="2026-06-20")
    assert adapter.compose_symbol(leg) == "SPY260620P00565500"


def test_compose_symbol_equities_passthrough():
    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    leg = MultilegLegSpec(symbol="SPY", asset_type="equities", side="buy", quantity=100)
    assert adapter.compose_symbol(leg) == "SPY"


def test_submit_multileg_order_calls_alpaca_with_mleg_class():
    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    mock_client = MagicMock()
    mock_order = MagicMock()
    mock_order.id = "parent-1"
    mock_order.legs = [
        MagicMock(id="leg-1", filled_avg_price="8.30", status="filled"),
        MagicMock(id="leg-2", filled_avg_price="4.20", status="filled"),
    ]
    mock_client.submit_order.return_value = mock_order
    legs = [_opt_leg("buy", "call", 560), _opt_leg("sell", "call", 570)]
    with patch.object(adapter, "_ensure_clients"):
        adapter._trading_client = mock_client
        result = adapter.submit_multileg_order(legs, order_type="limit", limit_price=4.0)
    submitted = mock_client.submit_order.call_args.args[0]
    # Inspect the request object — exact shape depends on alpaca-py version
    assert getattr(submitted, "order_class", None) is not None
    assert result.broker_order_id == "parent-1"
    assert result.atomic is True
    assert len(result.legs) == 2
    assert result.legs[0].status == "filled"
    assert result.legs[0].filled_price == 8.30


def test_list_option_expiries_returns_sorted_dates():
    from datetime import date
    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    mock_data = MagicMock()
    mock_data.get_option_contracts.return_value = MagicMock(option_contracts=[
        MagicMock(expiration_date=date(2026, 6, 20)),
        MagicMock(expiration_date=date(2026, 5, 16)),
        MagicMock(expiration_date=date(2026, 6, 20)),  # dup
    ])
    with patch.object(adapter, "_ensure_clients"):
        adapter._data_client = mock_data
        result = adapter.list_option_expiries("SPY")
    assert result == [date(2026, 5, 16), date(2026, 6, 20)]


def test_get_option_chain_maps_snapshot_to_contracts():
    from datetime import date, datetime, timezone
    adapter = AlpacaAdapter(api_key="k", secret_key="s")
    mock_data = MagicMock()
    # Stub: snapshot with one call
    snap = MagicMock()
    snap.snapshots = {
        "SPY260620C00560000": MagicMock(
            latest_quote=MagicMock(bid_price=8.2, ask_price=8.4, timestamp=datetime(2026, 5, 14, 15, 30, tzinfo=timezone.utc)),
            latest_trade=MagicMock(price=8.3),
            implied_volatility=0.30,
            greeks=MagicMock(delta=0.55, gamma=0.020, theta=-14.1, vega=48.0),
            open_interest=2345, daily_bar=MagicMock(volume=789),
        ),
    }
    mock_data.get_option_chain.return_value = snap
    # Spot lookup uses the trading data client too
    mock_data.get_stock_latest_trade.return_value = {"SPY": MagicMock(price=565.0)}
    with patch.object(adapter, "_ensure_clients"):
        adapter._data_client = mock_data
        chain = adapter.get_option_chain("SPY", date(2026, 6, 20))
    assert chain.underlying == "SPY"
    assert chain.spot == 565.0
    assert chain.expiry == date(2026, 6, 20)
    assert len(chain.contracts) == 1
    c = chain.contracts[0]
    assert c.strike == 560.0
    assert c.right == "call"
    assert c.bid == 8.2 and c.ask == 8.4
    assert c.iv == 0.30
    assert c.delta == 0.55
