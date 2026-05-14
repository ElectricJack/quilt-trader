from datetime import datetime, date
from sdk.models import Position, TradeFill, OptionContract, OptionChain


class TestTradeFill:
    def test_create_fill(self):
        fill = TradeFill(
            symbol="AAPL",
            side="buy",
            quantity=100,
            filled_price=150.25,
            fees=1.00,
            slippage=0.05,
            timestamp=datetime(2026, 5, 12, 10, 30, 0),
        )
        assert fill.symbol == "AAPL"
        assert fill.filled_price == 150.25
        assert fill.fees == 1.00
        assert fill.slippage == 0.05

    def test_fill_with_fee_breakdown(self):
        fill = TradeFill(
            symbol="BTC/USD",
            side="buy",
            quantity=0.05,
            filled_price=68000.00,
            fees=3.40,
            slippage=10.00,
            timestamp=datetime(2026, 5, 12, 10, 30, 0),
            fee_breakdown={"commission": 0.00, "exchange_fee": 3.40, "network_fee": 0.00},
        )
        assert fill.fee_breakdown["exchange_fee"] == 3.40

    def test_serialization_roundtrip(self):
        fill = TradeFill(
            symbol="AAPL",
            side="buy",
            quantity=100,
            filled_price=150.25,
            fees=1.00,
            slippage=0.05,
            timestamp=datetime(2026, 5, 12, 10, 30, 0),
        )
        d = fill.to_dict()
        restored = TradeFill.from_dict(d)
        assert restored.symbol == fill.symbol
        assert restored.filled_price == fill.filled_price


class TestPosition:
    def test_create_single_position(self):
        pos = Position(
            symbol="AAPL",
            quantity=100,
            avg_cost=150.00,
            current_price=155.00,
            asset_type="equities",
        )
        assert pos.symbol == "AAPL"
        assert pos.market_value == 15500.00
        assert pos.unrealized_pnl == 500.00
        assert pos.unrealized_pnl_pct == (155.00 - 150.00) / 150.00 * 100

    def test_short_position_pnl(self):
        pos = Position(
            symbol="TSLA",
            quantity=-50,
            avg_cost=200.00,
            current_price=190.00,
            asset_type="equities",
        )
        assert pos.market_value == -9500.00
        assert pos.unrealized_pnl == 500.00  # Shorted at 200, now 190 = profit

    def test_serialization_roundtrip(self):
        pos = Position(
            symbol="AAPL",
            quantity=100,
            avg_cost=150.00,
            current_price=155.00,
            asset_type="equities",
        )
        d = pos.to_dict()
        restored = Position.from_dict(d)
        assert restored.symbol == pos.symbol
        assert restored.quantity == pos.quantity
        assert restored.avg_cost == pos.avg_cost


class TestOptionContract:
    def test_create_call(self):
        contract = OptionContract(
            symbol="AAPL250620C00200000",
            underlying="AAPL",
            expiration=date(2025, 6, 20),
            strike=200.00,
            option_type="call",
            bid=3.50,
            ask=3.70,
            last=3.60,
            volume=1500,
            open_interest=12000,
            implied_volatility=0.32,
        )
        assert contract.option_type == "call"
        assert contract.strike == 200.00
        assert contract.mid == 3.60

    def test_create_put(self):
        contract = OptionContract(
            symbol="AAPL250620P00200000",
            underlying="AAPL",
            expiration=date(2025, 6, 20),
            strike=200.00,
            option_type="put",
            bid=2.10,
            ask=2.30,
            last=2.20,
            volume=800,
            open_interest=5000,
            implied_volatility=0.30,
        )
        assert contract.option_type == "put"
        assert contract.mid == 2.20


class TestOptionChain:
    def test_create_chain(self):
        call = OptionContract(
            symbol="AAPL250620C00200000",
            underlying="AAPL",
            expiration=date(2025, 6, 20),
            strike=200.00,
            option_type="call",
            bid=3.50, ask=3.70, last=3.60,
            volume=1500, open_interest=12000,
            implied_volatility=0.32,
        )
        put = OptionContract(
            symbol="AAPL250620P00200000",
            underlying="AAPL",
            expiration=date(2025, 6, 20),
            strike=200.00,
            option_type="put",
            bid=2.10, ask=2.30, last=2.20,
            volume=800, open_interest=5000,
            implied_volatility=0.30,
        )
        chain = OptionChain(
            underlying="AAPL",
            expiration=date(2025, 6, 20),
            calls=[call],
            puts=[put],
        )
        assert chain.underlying == "AAPL"
        assert len(chain.calls) == 1
        assert len(chain.puts) == 1

    def test_get_strike(self):
        call = OptionContract(
            symbol="AAPL250620C00200000",
            underlying="AAPL",
            expiration=date(2025, 6, 20),
            strike=200.00,
            option_type="call",
            bid=3.50, ask=3.70, last=3.60,
            volume=1500, open_interest=12000,
            implied_volatility=0.32,
        )
        chain = OptionChain(
            underlying="AAPL",
            expiration=date(2025, 6, 20),
            calls=[call],
            puts=[],
        )
        found = chain.get_call(200.00)
        assert found is not None
        assert found.strike == 200.00
        assert chain.get_call(999.00) is None

    def test_strikes(self):
        calls = [
            OptionContract(
                symbol=f"AAPL250620C{int(s*1000):08d}",
                underlying="AAPL", expiration=date(2025, 6, 20),
                strike=s, option_type="call",
                bid=1.0, ask=1.5, last=1.25,
                volume=100, open_interest=500,
                implied_volatility=0.30,
            )
            for s in [195.0, 200.0, 205.0]
        ]
        chain = OptionChain(
            underlying="AAPL",
            expiration=date(2025, 6, 20),
            calls=calls,
            puts=[],
        )
        assert chain.strikes == [195.0, 200.0, 205.0]
