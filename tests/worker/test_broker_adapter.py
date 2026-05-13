import pytest
from worker.broker_adapter import BrokerAdapter, MockBrokerAdapter, OrderResult


def test_mock_broker_get_positions():
    broker = MockBrokerAdapter()
    broker.set_positions({"AAPL": {"symbol": "AAPL", "quantity": 100, "avg_cost": 150.0, "current_price": 155.0}})
    positions = broker.get_positions()
    assert "AAPL" in positions
    assert positions["AAPL"]["quantity"] == 100


def test_mock_broker_get_account_info():
    broker = MockBrokerAdapter()
    broker.set_account_info(cash=50000.0, portfolio_value=75000.0, buying_power=100000.0)
    info = broker.get_account_info()
    assert info["cash"] == 50000.0
    assert info["portfolio_value"] == 75000.0
    assert info["buying_power"] == 100000.0


def test_mock_broker_submit_order():
    broker = MockBrokerAdapter()
    broker.set_fill_price(151.0)
    result = broker.submit_order(symbol="AAPL", side="buy", quantity=100, order_type="market")
    assert isinstance(result, OrderResult)
    assert result.filled_price == 151.0
    assert result.quantity == 100
    assert result.symbol == "AAPL"
    assert result.fees == 0.0


def test_mock_broker_submit_order_with_fees():
    broker = MockBrokerAdapter()
    broker.set_fill_price(200.0)
    broker.set_fees(1.50)
    result = broker.submit_order(symbol="TSLA", side="sell", quantity=50, order_type="limit", limit_price=200.0)
    assert result.fees == 1.50
    assert result.filled_price == 200.0


def test_mock_broker_order_history():
    broker = MockBrokerAdapter()
    broker.set_fill_price(150.0)
    broker.submit_order(symbol="AAPL", side="buy", quantity=100, order_type="market")
    broker.submit_order(symbol="TSLA", side="buy", quantity=50, order_type="market")
    assert len(broker.order_history) == 2
    assert broker.order_history[0].symbol == "AAPL"
    assert broker.order_history[1].symbol == "TSLA"


def test_broker_adapter_is_abstract():
    with pytest.raises(TypeError):
        BrokerAdapter()
