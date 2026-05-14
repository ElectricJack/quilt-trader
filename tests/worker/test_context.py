import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock
import pandas as pd
from worker.context import LiveTickContext
from worker.broker_adapter import MockBrokerAdapter


@pytest.fixture
def mock_broker():
    broker = MockBrokerAdapter()
    broker.set_positions({"AAPL": {"symbol": "AAPL", "quantity": 100, "avg_cost": 150.0, "current_price": 155.0}})
    broker.set_account_info(cash=50000.0, portfolio_value=75000.0, buying_power=100000.0)
    return broker


@pytest.fixture
def mock_data_client():
    client = AsyncMock()
    client.get_market_data.return_value = pd.DataFrame({
        "timestamp": ["2025-01-01T09:30:00", "2025-01-01T09:31:00"],
        "open": [150.0, 150.5], "high": [151.0, 152.0],
        "low": [149.0, 150.0], "close": [150.5, 151.0], "volume": [1000, 1500],
    })
    client.get_custom_data.return_value = pd.DataFrame({"symbol": ["TSLA", "NVDA"], "score": [0.95, 0.88]})
    return client


def test_tick_context_timestamp(mock_broker, mock_data_client):
    ts = datetime(2025, 1, 1, 9, 30, tzinfo=timezone.utc)
    ctx = LiveTickContext(timestamp=ts, mode="live", broker=mock_broker, data_client=mock_data_client)
    assert ctx.timestamp == ts
    assert ctx.mode == "live"


def test_tick_context_positions(mock_broker, mock_data_client):
    ctx = LiveTickContext(timestamp=datetime.now(timezone.utc), mode="live", broker=mock_broker, data_client=mock_data_client)
    positions = ctx.positions
    assert "AAPL" in positions
    assert positions["AAPL"]["quantity"] == 100


def test_tick_context_account_values(mock_broker, mock_data_client):
    ctx = LiveTickContext(timestamp=datetime.now(timezone.utc), mode="live", broker=mock_broker, data_client=mock_data_client)
    assert ctx.account_value == 75000.0
    assert ctx.cash == 50000.0
    assert ctx.buying_power == 100000.0


@pytest.mark.asyncio
async def test_tick_context_market_data(mock_broker, mock_data_client):
    ctx = LiveTickContext(timestamp=datetime.now(timezone.utc), mode="live", broker=mock_broker, data_client=mock_data_client)
    df = await ctx.market_data("AAPL", timeframe="1min", bars=100)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    mock_data_client.get_market_data.assert_called_once_with("AAPL", timeframe="1min", bars=100)


@pytest.mark.asyncio
async def test_tick_context_custom_data(mock_broker, mock_data_client):
    ctx = LiveTickContext(timestamp=datetime.now(timezone.utc), mode="live", broker=mock_broker, data_client=mock_data_client)
    df = await ctx.data("alpha-picks")
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    mock_data_client.get_custom_data.assert_called_once_with("alpha-picks")
