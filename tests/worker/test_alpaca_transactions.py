"""Tests for AlpacaAdapter.get_transactions (httpx-based, separate from SDK)."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

from worker.alpaca_adapter import AlpacaAdapter, _map_alpaca_activity


def _mock_response(payload):
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


class TestMapAlpacaActivity:
    def test_fill_buy(self):
        raw = {
            "id": "abc123",
            "activity_type": "FILL",
            "transaction_time": "2026-04-01T15:30:00Z",
            "symbol": "AAPL",
            "side": "buy",
            "qty": "10",
            "price": "150.00",
        }
        txn = _map_alpaca_activity(raw)
        assert txn is not None
        assert txn.type == "fill"
        assert txn.symbol == "AAPL"
        assert txn.side == "buy"
        assert txn.quantity == 10.0
        assert txn.price == 150.0
        assert txn.amount == -1500.0  # buy = cash out
        assert txn.broker_id == "abc123"

    def test_fill_sell_amount_positive(self):
        raw = {
            "id": "x",
            "activity_type": "FILL",
            "transaction_time": "2026-04-01T15:30:00Z",
            "symbol": "MSFT",
            "side": "sell",
            "qty": "5",
            "price": "400",
        }
        txn = _map_alpaca_activity(raw)
        assert txn is not None
        assert txn.amount == 2000.0

    def test_dividend(self):
        raw = {
            "id": "d1",
            "activity_type": "DIV",
            "date": "2026-04-15",
            "net_amount": "12.50",
            "symbol": "AAPL",
        }
        txn = _map_alpaca_activity(raw)
        assert txn is not None
        assert txn.type == "dividend"
        assert txn.amount == 12.5
        assert txn.symbol == "AAPL"

    def test_cash_deposit(self):
        raw = {"id": "c1", "activity_type": "CSD", "date": "2026-03-01", "net_amount": "5000"}
        txn = _map_alpaca_activity(raw)
        assert txn is not None
        assert txn.type == "deposit"
        assert txn.amount == 5000.0

    def test_cash_withdrawal(self):
        raw = {"id": "c2", "activity_type": "CSW", "date": "2026-03-05", "net_amount": "-200"}
        txn = _map_alpaca_activity(raw)
        assert txn is not None
        assert txn.type == "withdrawal"
        assert txn.amount == -200.0

    def test_journal_cash_positive_is_deposit(self):
        raw = {"id": "j1", "activity_type": "JNLC", "date": "2026-03-01", "net_amount": "100"}
        txn = _map_alpaca_activity(raw)
        assert txn is not None
        assert txn.type == "deposit"

    def test_journal_cash_negative_is_withdrawal(self):
        raw = {"id": "j2", "activity_type": "JNLC", "date": "2026-03-01", "net_amount": "-100"}
        txn = _map_alpaca_activity(raw)
        assert txn is not None
        assert txn.type == "withdrawal"

    def test_unknown_type_returns_none(self):
        assert _map_alpaca_activity({"id": "s", "activity_type": "SPIN"}) is None

    def test_missing_id_returns_none(self):
        assert _map_alpaca_activity({"activity_type": "FILL"}) is None


class TestAlpacaGetTransactions:
    def test_paginates_and_stops_on_short_page(self):
        adapter = AlpacaAdapter(api_key="k", secret_key="s", paper=True)
        http = MagicMock()
        # Single page with one activity (less than 100 → stop).
        http.get.return_value = _mock_response([
            {
                "id": "fill-1",
                "activity_type": "FILL",
                "transaction_time": "2026-05-01T10:00:00Z",
                "symbol": "AAPL",
                "side": "buy",
                "qty": "1",
                "price": "200",
            }
        ])
        adapter._http = http
        txns = adapter.get_transactions(datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert len(txns) == 1
        assert txns[0].symbol == "AAPL"
        http.get.assert_called_once()

    def test_empty_response(self):
        adapter = AlpacaAdapter(api_key="k", secret_key="s", paper=True)
        http = MagicMock()
        http.get.return_value = _mock_response([])
        adapter._http = http
        assert adapter.get_transactions(datetime(2026, 1, 1, tzinfo=timezone.utc)) == []
