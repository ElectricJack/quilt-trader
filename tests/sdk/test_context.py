import pytest
import pandas as pd
from datetime import datetime, date
from sdk.context import TickContext
from sdk.models import Position, OptionChain, OptionContract


class MockTickContext(TickContext):
    """Concrete implementation for testing the abstract interface."""

    def __init__(self):
        self._timestamp = datetime(2026, 5, 12, 10, 30, 0)
        self._mode = "live"
        self._positions = {
            "AAPL": Position("AAPL", 100, 150.00, 155.00),
        }
        self._account_value = 100000.00
        self._cash = 85000.00
        self._buying_power = 170000.00

    @property
    def timestamp(self):
        return self._timestamp

    @property
    def mode(self):
        return self._mode

    @property
    def positions(self):
        return dict(self._positions)

    @property
    def account_value(self):
        return self._account_value

    @property
    def cash(self):
        return self._cash

    @property
    def buying_power(self):
        return self._buying_power

    def market_data(self, symbol, timeframe="1min", bars=100):
        return pd.DataFrame({
            "open": [150.0], "high": [155.0], "low": [149.0],
            "close": [154.0], "volume": [1000000],
            "timestamp": [self._timestamp],
        })

    def data(self, source_name):
        return pd.DataFrame({"symbol": ["AAPL"], "score": [0.8]})

    def option_chain(self, symbol, expiration=None):
        call = OptionContract(
            symbol="AAPL250620C00200000", underlying="AAPL",
            expiration=date(2025, 6, 20), strike=200.00, option_type="call",
            bid=3.50, ask=3.70, last=3.60,
            volume=1500, open_interest=12000, implied_volatility=0.32,
        )
        return OptionChain(underlying=symbol, expiration=date(2025, 6, 20), calls=[call], puts=[])


class TestTickContext:
    def test_timestamp(self):
        ctx = MockTickContext()
        assert ctx.timestamp == datetime(2026, 5, 12, 10, 30, 0)

    def test_mode(self):
        ctx = MockTickContext()
        assert ctx.mode == "live"

    def test_positions(self):
        ctx = MockTickContext()
        positions = ctx.positions
        assert "AAPL" in positions
        assert positions["AAPL"].quantity == 100

    def test_account_value(self):
        ctx = MockTickContext()
        assert ctx.account_value == 100000.00

    def test_cash(self):
        ctx = MockTickContext()
        assert ctx.cash == 85000.00

    def test_buying_power(self):
        ctx = MockTickContext()
        assert ctx.buying_power == 170000.00

    def test_market_data_returns_dataframe(self):
        ctx = MockTickContext()
        df = ctx.market_data("AAPL")
        assert isinstance(df, pd.DataFrame)
        assert "close" in df.columns

    def test_data_returns_dataframe(self):
        ctx = MockTickContext()
        df = ctx.data("alpha-picks")
        assert isinstance(df, pd.DataFrame)

    def test_option_chain(self):
        ctx = MockTickContext()
        chain = ctx.option_chain("AAPL")
        assert chain.underlying == "AAPL"
        assert len(chain.calls) == 1

    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            TickContext()
