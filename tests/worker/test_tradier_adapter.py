from unittest.mock import MagicMock, patch

import pytest

from worker.tradier_adapter import TradierAdapter
from worker.broker_adapter import MultilegLegSpec, OrderResult


def _mock_response(json_payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = json_payload
    resp.raise_for_status.return_value = None
    return resp


def _opt_leg(side, right, strike, expiry="2026-06-20"):
    return MultilegLegSpec(symbol="SPY", asset_type="options", side=side,
                           quantity=1, expiry=expiry, strike=strike, right=right)


class TestTradierMultilegSupport:
    def test_supports_multileg_same_underlying_options(self):
        adapter = TradierAdapter(access_token="t", account_id="VA1", paper=True)
        legs = [_opt_leg("buy", "call", 560), _opt_leg("sell", "call", 570)]
        assert adapter.supports_multileg_orders(legs) is True

    def test_supports_multileg_false_for_mixed_underlyings(self):
        adapter = TradierAdapter(access_token="t", account_id="VA1", paper=True)
        leg1 = _opt_leg("buy", "call", 560)
        leg2 = MultilegLegSpec(symbol="QQQ", asset_type="options", side="sell",
                               quantity=1, expiry="2026-06-20", strike=450, right="call")
        assert adapter.supports_multileg_orders([leg1, leg2]) is False

    def test_supports_multileg_false_for_non_options(self):
        adapter = TradierAdapter(access_token="t", account_id="VA1", paper=True)
        leg = MultilegLegSpec(symbol="SPY", asset_type="equities", side="buy", quantity=100)
        assert adapter.supports_multileg_orders([leg, leg]) is False

    def test_supports_multileg_false_for_single_leg(self):
        adapter = TradierAdapter(access_token="t", account_id="VA1", paper=True)
        leg = _opt_leg("buy", "call", 560)
        assert adapter.supports_multileg_orders([leg]) is False


class TestTradierComposeSymbol:
    def test_compose_symbol_call_occ(self):
        adapter = TradierAdapter(access_token="t", account_id="VA1", paper=True)
        leg = _opt_leg("buy", "call", 560.0, expiry="2026-06-20")
        assert adapter.compose_symbol(leg) == "SPY260620C00560000"

    def test_compose_symbol_put_occ(self):
        adapter = TradierAdapter(access_token="t", account_id="VA1", paper=True)
        leg = _opt_leg("sell", "put", 565.50, expiry="2026-06-20")
        assert adapter.compose_symbol(leg) == "SPY260620P00565500"

    def test_compose_symbol_equities_passthrough(self):
        adapter = TradierAdapter(access_token="t", account_id="VA1", paper=True)
        leg = MultilegLegSpec(symbol="SPY", asset_type="equities", side="buy", quantity=100)
        assert adapter.compose_symbol(leg) == "SPY"


class TestTradierSubmitMultilegOrder:
    def test_submit_multileg_order_posts_class_multileg(self):
        adapter = TradierAdapter(access_token="t", account_id="VA1", paper=True)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"order": {"id": 12345, "status": "ok"}}
        mock_resp.raise_for_status = lambda: None
        legs = [_opt_leg("buy", "call", 560), _opt_leg("sell", "call", 570)]
        with patch("requests.post", return_value=mock_resp) as p:
            result = adapter.submit_multileg_order(legs, order_type="limit", limit_price=4.0)
        posted_data = p.call_args.kwargs["data"]
        assert posted_data["class"] == "multileg"
        assert posted_data["symbol"] == "SPY"
        assert posted_data["type"] == "debit"            # limit becomes debit/credit per net price
        assert posted_data["price"] == "4.00"
        # Per-leg fields
        assert posted_data["option_symbol[0]"] == "SPY260620C00560000"
        assert posted_data["side[0]"] == "buy_to_open"
        assert posted_data["quantity[0]"] == "1"
        assert posted_data["option_symbol[1]"] == "SPY260620C00570000"
        assert posted_data["side[1]"] == "sell_to_open"
        assert result.broker_order_id == "12345"
        assert result.atomic is True

    def test_submit_multileg_credit_when_more_sells(self):
        adapter = TradierAdapter(access_token="t", account_id="VA1", paper=True)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"order": {"id": 99}}
        mock_resp.raise_for_status = lambda: None
        # Two sells, one buy -> net negative -> credit.
        legs = [
            _opt_leg("sell", "call", 560),
            _opt_leg("sell", "call", 570),
            _opt_leg("buy", "call", 580),
        ]
        with patch("requests.post", return_value=mock_resp) as p:
            adapter.submit_multileg_order(legs, order_type="limit", limit_price=2.5)
        posted_data = p.call_args.kwargs["data"]
        assert posted_data["type"] == "credit"

    def test_submit_multileg_market_order_omits_price(self):
        adapter = TradierAdapter(access_token="t", account_id="VA1", paper=True)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"order": {"id": 77}}
        mock_resp.raise_for_status = lambda: None
        legs = [_opt_leg("buy", "call", 560), _opt_leg("sell", "call", 570)]
        with patch("requests.post", return_value=mock_resp) as p:
            adapter.submit_multileg_order(legs, order_type="market", limit_price=None)
        posted_data = p.call_args.kwargs["data"]
        assert posted_data["type"] == "market"
        assert "price" not in posted_data


class TestTradierOptionsChain:
    def test_list_option_expiries_returns_sorted_dates(self):
        from datetime import date
        adapter = TradierAdapter(access_token="t", account_id="VA1", paper=True)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "expirations": {"date": ["2026-06-20", "2026-05-16"]},
        }
        mock_resp.raise_for_status = lambda: None
        with patch("requests.get", return_value=mock_resp):
            out = adapter.list_option_expiries("SPY")
        assert out == [date(2026, 5, 16), date(2026, 6, 20)]

    def test_list_option_expiries_handles_single_string(self):
        from datetime import date
        adapter = TradierAdapter(access_token="t", account_id="VA1", paper=True)
        mock_resp = MagicMock()
        # Tradier sometimes returns a bare string for a single expiry.
        mock_resp.json.return_value = {"expirations": {"date": "2026-05-16"}}
        mock_resp.raise_for_status = lambda: None
        with patch("requests.get", return_value=mock_resp):
            out = adapter.list_option_expiries("SPY")
        assert out == [date(2026, 5, 16)]

    def test_list_option_expiries_handles_empty(self):
        adapter = TradierAdapter(access_token="t", account_id="VA1", paper=True)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"expirations": None}
        mock_resp.raise_for_status = lambda: None
        with patch("requests.get", return_value=mock_resp):
            out = adapter.list_option_expiries("SPY")
        assert out == []

    def test_get_option_chain_maps_strikes_and_greeks(self):
        from datetime import date
        adapter = TradierAdapter(access_token="t", account_id="VA1", paper=True)
        chain_resp = MagicMock()
        chain_resp.json.return_value = {
            "options": {
                "option": [
                    {
                        "symbol": "SPY260620C00560000", "strike": 560.0, "option_type": "call",
                        "bid": 8.2, "ask": 8.4, "last": 8.3,
                        "greeks": {"mid_iv": 0.30, "delta": 0.55, "gamma": 0.020,
                                   "theta": -14.1, "vega": 48.0},
                        "open_interest": 2345, "volume": 789,
                    },
                    {
                        "symbol": "SPY260620P00560000", "strike": 560.0, "option_type": "put",
                        "bid": 1.1, "ask": 1.3, "last": 1.2,
                        "greeks": {"mid_iv": 0.32, "delta": -0.45, "gamma": 0.020,
                                   "theta": -12.0, "vega": 48.0},
                        "open_interest": 1100, "volume": 200,
                    },
                ]
            }
        }
        chain_resp.raise_for_status = lambda: None
        spot_resp = MagicMock()
        spot_resp.json.return_value = {"quotes": {"quote": {"last": 565.0}}}
        spot_resp.raise_for_status = lambda: None
        with patch("requests.get", side_effect=[spot_resp, chain_resp]):
            chain = adapter.get_option_chain("SPY", date(2026, 6, 20))
        assert chain.spot == 565.0
        assert chain.underlying == "SPY"
        assert chain.expiry == date(2026, 6, 20)
        assert len(chain.contracts) == 2
        assert chain.contracts[0].right == "call"
        assert chain.contracts[1].right == "put"
        assert chain.contracts[0].iv == 0.30
        assert chain.contracts[0].delta == 0.55
        assert chain.contracts[0].open_interest == 2345
        assert chain.contracts[0].volume == 789

    def test_get_option_chain_handles_single_option_dict(self):
        from datetime import date
        adapter = TradierAdapter(access_token="t", account_id="VA1", paper=True)
        chain_resp = MagicMock()
        chain_resp.json.return_value = {
            "options": {
                "option": {
                    "symbol": "SPY260620C00560000", "strike": 560.0, "option_type": "call",
                    "bid": 8.2, "ask": 8.4, "last": 8.3,
                    "greeks": {"mid_iv": 0.30, "delta": 0.55, "gamma": 0.020,
                               "theta": -14.1, "vega": 48.0},
                    "open_interest": 2345, "volume": 789,
                }
            }
        }
        chain_resp.raise_for_status = lambda: None
        spot_resp = MagicMock()
        spot_resp.json.return_value = {"quotes": {"quote": {"last": 565.0}}}
        spot_resp.raise_for_status = lambda: None
        with patch("requests.get", side_effect=[spot_resp, chain_resp]):
            chain = adapter.get_option_chain("SPY", date(2026, 6, 20))
        assert len(chain.contracts) == 1
        assert chain.contracts[0].strike == 560.0

    def test_get_option_chain_sorts_contracts(self):
        from datetime import date
        adapter = TradierAdapter(access_token="t", account_id="VA1", paper=True)
        chain_resp = MagicMock()
        chain_resp.json.return_value = {
            "options": {
                "option": [
                    {"symbol": "X", "strike": 570.0, "option_type": "put",
                     "bid": None, "ask": None, "last": None, "greeks": {},
                     "open_interest": None, "volume": None},
                    {"symbol": "Y", "strike": 560.0, "option_type": "put",
                     "bid": None, "ask": None, "last": None, "greeks": {},
                     "open_interest": None, "volume": None},
                    {"symbol": "Z", "strike": 560.0, "option_type": "call",
                     "bid": None, "ask": None, "last": None, "greeks": {},
                     "open_interest": None, "volume": None},
                ]
            }
        }
        chain_resp.raise_for_status = lambda: None
        spot_resp = MagicMock()
        spot_resp.json.return_value = {"quotes": {"quote": {"last": 565.0}}}
        spot_resp.raise_for_status = lambda: None
        with patch("requests.get", side_effect=[spot_resp, chain_resp]):
            chain = adapter.get_option_chain("SPY", date(2026, 6, 20))
        # Sorted by (strike, right): 560/call, 560/put, 570/put
        assert (chain.contracts[0].strike, chain.contracts[0].right) == (560.0, "call")
        assert (chain.contracts[1].strike, chain.contracts[1].right) == (560.0, "put")
        assert (chain.contracts[2].strike, chain.contracts[2].right) == (570.0, "put")


class TestTradierAdapter:
    def test_init_sandbox_base_url(self):
        adapter = TradierAdapter(access_token="t", account_id="A1", sandbox=True)
        assert "sandbox.tradier.com" in adapter._base_url

    def test_init_live_base_url(self):
        adapter = TradierAdapter(access_token="t", account_id="A1", sandbox=False)
        assert adapter._base_url.startswith("https://api.tradier.com")

    def test_get_account_info_maps_balances(self):
        adapter = TradierAdapter(access_token="t", account_id="A1")
        mock_client = MagicMock()
        mock_client.get.return_value = _mock_response({
            "balances": {
                "total_equity": "75000.00",
                "total_cash": "20000.00",
                "margin": {"stock_buying_power": "150000.00"},
            }
        })
        adapter._client = mock_client
        info = adapter.get_account_info()
        assert info["cash"] == 20000.0
        assert info["portfolio_value"] == 75000.0
        assert info["buying_power"] == 150000.0
        assert info["currency"] == "USD"
        mock_client.get.assert_called_once_with("/accounts/A1/balances")

    def test_get_account_info_cash_account_fallback(self):
        adapter = TradierAdapter(access_token="t", account_id="A1")
        mock_client = MagicMock()
        mock_client.get.return_value = _mock_response({
            "balances": {
                "total_equity": "10000.00",
                "total_cash": "10000.00",
                "cash": {"cash_available": "10000.00"},
            }
        })
        adapter._client = mock_client
        info = adapter.get_account_info()
        assert info["buying_power"] == 10000.0

    def test_get_positions_empty(self):
        adapter = TradierAdapter(access_token="t", account_id="A1")
        mock_client = MagicMock()
        mock_client.get.return_value = _mock_response({"positions": "null"})
        adapter._client = mock_client
        assert adapter.get_positions() == {}

    def test_get_positions_single_item_dict(self):
        adapter = TradierAdapter(access_token="t", account_id="A1")
        mock_client = MagicMock()
        mock_client.get.return_value = _mock_response({
            "positions": {
                "position": {"symbol": "AAPL", "quantity": "10", "cost_basis": "1500.00"}
            }
        })
        adapter._client = mock_client
        positions = adapter.get_positions()
        assert "AAPL" in positions
        assert positions["AAPL"]["quantity"] == 10.0
        assert positions["AAPL"]["avg_price"] == 150.0
        assert positions["AAPL"]["side"] == "long"

    def test_get_positions_multiple_items_list(self):
        adapter = TradierAdapter(access_token="t", account_id="A1")
        mock_client = MagicMock()
        mock_client.get.return_value = _mock_response({
            "positions": {
                "position": [
                    {"symbol": "AAPL", "quantity": "10", "cost_basis": "1500.00"},
                    {"symbol": "MSFT", "quantity": "-5", "cost_basis": "1000.00"},
                ]
            }
        })
        adapter._client = mock_client
        positions = adapter.get_positions()
        assert positions["AAPL"]["side"] == "long"
        assert positions["MSFT"]["side"] == "short"
        assert positions["MSFT"]["quantity"] == -5.0

    def test_submit_market_order_posts_to_orders(self):
        adapter = TradierAdapter(access_token="t", account_id="A1")
        mock_client = MagicMock()
        mock_client.post.return_value = _mock_response({"order": {"id": "12345"}})
        adapter._client = mock_client

        result = adapter.submit_order(
            symbol="AAPL", side="buy", quantity=10.0, order_type="market"
        )
        assert isinstance(result, OrderResult)
        assert result.broker_order_id == "12345"
        assert result.symbol == "AAPL"
        called_path, _ = mock_client.post.call_args[0], mock_client.post.call_args[1]
        assert called_path[0] == "/accounts/A1/orders"
        sent_body = mock_client.post.call_args.kwargs["data"]
        assert sent_body["symbol"] == "AAPL"
        assert sent_body["side"] == "buy"
        assert sent_body["type"] == "market"
        assert sent_body["quantity"] == "10"

    def test_submit_limit_order_includes_price(self):
        adapter = TradierAdapter(access_token="t", account_id="A1")
        mock_client = MagicMock()
        mock_client.post.return_value = _mock_response({"order": {"id": "9"}})
        adapter._client = mock_client

        adapter.submit_order(
            symbol="MSFT", side="sell", quantity=2.0, order_type="limit", limit_price=400.5
        )
        body = mock_client.post.call_args.kwargs["data"]
        assert body["type"] == "limit"
        assert body["price"] == "400.5"

    def test_submit_order_rejects_bad_side(self):
        adapter = TradierAdapter(access_token="t", account_id="A1")
        adapter._client = MagicMock()
        with pytest.raises(ValueError, match="side"):
            adapter.submit_order(symbol="AAPL", side="hold", quantity=1.0, order_type="market")

    def test_close_disposes_client(self):
        adapter = TradierAdapter(access_token="t", account_id="A1")
        mock_client = MagicMock()
        adapter._client = mock_client
        adapter.close()
        mock_client.close.assert_called_once()
        assert adapter._client is None

    def test_get_transactions_maps_trades_and_dividends(self):
        from datetime import datetime, timedelta, timezone
        adapter = TradierAdapter(access_token="t", account_id="A1")
        mock_client = MagicMock()
        # First window returns the events; any subsequent window returns empty.
        mock_client.get.side_effect = [
            _mock_response({
                "history": {
                    "event": [
                        {
                            "amount": -1500.0,
                            "date": "2026-04-01T15:30:00.000Z",
                            "type": "trade",
                            "trade": {
                                "symbol": "AAPL",
                                "quantity": 10,
                                "price": 150.0,
                                "trade_type": "buy",
                            },
                        },
                        {
                            "amount": 12.50,
                            "date": "2026-04-15T00:00:00.000Z",
                            "type": "dividend",
                            "dividend": {"symbol": "AAPL"},
                            "description": "Cash dividend",
                        },
                        {
                            "amount": 5000.0,
                            "date": "2026-03-01T00:00:00.000Z",
                            "type": "ach",
                            "description": "Deposit",
                        },
                    ]
                }
            }),
            *(_mock_response({"history": "null"}) for _ in range(20)),
        ]
        adapter._client = mock_client
        # Use a small recent window so only one 30-day slice is needed.
        since = datetime.now(timezone.utc) - timedelta(days=5)
        txns = adapter.get_transactions(since)
        assert len(txns) == 3
        fill = next(t for t in txns if t.type == "fill")
        assert fill.symbol == "AAPL"
        assert fill.quantity == 10.0
        assert fill.side == "buy"
        assert fill.amount == -1500.0
        div = next(t for t in txns if t.type == "dividend")
        assert div.symbol == "AAPL"
        assert div.amount == 12.5
        dep = next(t for t in txns if t.type == "deposit")
        assert dep.amount == 5000.0

    def test_get_transactions_handles_empty(self):
        from datetime import datetime, timedelta, timezone
        adapter = TradierAdapter(access_token="t", account_id="A1")
        mock_client = MagicMock()
        mock_client.get.return_value = _mock_response({"history": "null"})
        adapter._client = mock_client
        assert adapter.get_transactions(datetime.now(timezone.utc) - timedelta(days=5)) == []

    def test_get_transactions_single_event_dict(self):
        from datetime import datetime, timedelta, timezone
        adapter = TradierAdapter(access_token="t", account_id="A1")
        mock_client = MagicMock()
        mock_client.get.side_effect = [
            _mock_response({
                "history": {
                    "event": {
                        "amount": -200.0,
                        "date": "2026-05-01T10:00:00.000Z",
                        "type": "ach",
                        "description": "Withdrawal",
                    }
                }
            }),
            *(_mock_response({"history": "null"}) for _ in range(20)),
        ]
        adapter._client = mock_client
        since = datetime.now(timezone.utc) - timedelta(days=5)
        txns = adapter.get_transactions(since)
        assert len(txns) == 1
        assert txns[0].type == "withdrawal"
        assert txns[0].amount == -200.0

    def test_get_transactions_windows_long_range(self):
        """Verify multiple HTTP calls are made for a >30 day range."""
        from datetime import datetime, timedelta, timezone
        adapter = TradierAdapter(access_token="t", account_id="A1")
        mock_client = MagicMock()
        mock_client.get.return_value = _mock_response({"history": "null"})
        adapter._client = mock_client
        since = datetime.now(timezone.utc) - timedelta(days=100)
        adapter.get_transactions(since)
        # 100 days / 30-day windows = 4 calls.
        assert mock_client.get.call_count >= 3
