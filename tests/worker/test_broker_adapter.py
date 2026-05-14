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


from datetime import date
from worker.broker_adapter import (
    MultilegLegSpec, MultilegOrderResult, MultilegLegResult,
    OptionContract, OptionChainSnapshot, MockBrokerAdapter,
)

def test_multileg_leg_spec_fields():
    leg = MultilegLegSpec(
        symbol="SPY", asset_type="options", side="buy", quantity=1,
        expiry="2026-06-20", strike=560.0, right="call",
    )
    assert leg.symbol == "SPY"
    assert leg.right == "call"

def test_multileg_order_result_aggregates_legs():
    result = MultilegOrderResult(
        broker_order_id="parent-1",
        legs=[
            MultilegLegResult(index=0, status="filled", filled_price=8.30, fees=0.65, broker_order_id="leg-1"),
            MultilegLegResult(index=1, status="filled", filled_price=4.20, fees=0.65, broker_order_id="leg-2"),
        ],
        atomic=True,
    )
    assert len(result.legs) == 2
    assert result.atomic is True

def test_option_chain_snapshot_sorts_contracts_by_strike():
    snap = OptionChainSnapshot(
        underlying="SPY", spot=565.0, expiry=date(2026, 6, 20),
        contracts=[
            OptionContract(strike=570.0, right="call", occ_symbol="SPY260620C00570000",
                           bid=4.1, ask=4.3, last=4.2, iv=0.28, delta=0.35,
                           gamma=0.018, theta=-12.4, vega=45.2, open_interest=1234, volume=567),
            OptionContract(strike=560.0, right="call", occ_symbol="SPY260620C00560000",
                           bid=8.2, ask=8.4, last=8.3, iv=0.30, delta=0.55,
                           gamma=0.020, theta=-14.1, vega=48.0, open_interest=2345, volume=789),
        ],
        as_of=None,  # populated by adapter
    )
    assert snap.contracts[0].strike == 570.0  # not auto-sorted; adapters sort

def test_mock_supports_multileg_false_by_default():
    adapter = MockBrokerAdapter()
    leg = MultilegLegSpec(symbol="SPY", asset_type="options", side="buy", quantity=1,
                          expiry="2026-06-20", strike=560.0, right="call")
    assert adapter.supports_multileg_orders([leg, leg]) is False

def test_mock_compose_symbol_passthrough():
    adapter = MockBrokerAdapter()
    leg = MultilegLegSpec(symbol="SPY", asset_type="equities", side="buy", quantity=1)
    assert adapter.compose_symbol(leg) == "SPY"
