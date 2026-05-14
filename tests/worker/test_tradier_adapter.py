from unittest.mock import MagicMock, patch

import pytest

from worker.tradier_adapter import TradierAdapter
from worker.broker_adapter import OrderResult


def _mock_response(json_payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = json_payload
    resp.raise_for_status.return_value = None
    return resp


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
