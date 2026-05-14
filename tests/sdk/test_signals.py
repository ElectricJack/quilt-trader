from sdk.signals import SignalType, OrderType, SignalLeg, Signal


class TestSignalType:
    def test_enum_values(self):
        assert SignalType.BUY.value == "buy"
        assert SignalType.SELL.value == "sell"
        assert SignalType.SELL_SHORT.value == "sell_short"
        assert SignalType.BUY_TO_COVER.value == "buy_to_cover"


class TestOrderType:
    def test_enum_values(self):
        assert OrderType.MARKET.value == "market"
        assert OrderType.LIMIT.value == "limit"
        assert OrderType.STOP.value == "stop"
        assert OrderType.STOP_LIMIT.value == "stop_limit"


class TestSignalLeg:
    def test_create_equity_leg(self):
        leg = SignalLeg(
            symbol="AAPL",
            signal_type=SignalType.BUY,
            quantity=100,
        )
        assert leg.symbol == "AAPL"
        assert leg.signal_type == SignalType.BUY
        assert leg.quantity == 100
        assert leg.asset_type == "equities"
        assert leg.order_type == OrderType.MARKET
        assert leg.limit_price is None
        assert leg.stop_price is None

    def test_create_options_leg_with_limit(self):
        leg = SignalLeg(
            symbol="AAPL250620C00200000",
            signal_type=SignalType.BUY,
            quantity=1,
            asset_type="options",
            order_type=OrderType.LIMIT,
            limit_price=3.50,
        )
        assert leg.asset_type == "options"
        assert leg.order_type == OrderType.LIMIT
        assert leg.limit_price == 3.50

    def test_create_crypto_leg_fractional(self):
        leg = SignalLeg(
            symbol="BTC/USD",
            signal_type=SignalType.BUY,
            quantity=0.00142,
            asset_type="crypto",
        )
        assert leg.quantity == 0.00142
        assert leg.asset_type == "crypto"

    def test_serialization_roundtrip(self):
        leg = SignalLeg(
            symbol="AAPL",
            signal_type=SignalType.BUY,
            quantity=100,
            order_type=OrderType.LIMIT,
            limit_price=150.00,
        )
        d = leg.to_dict()
        restored = SignalLeg.from_dict(d)
        assert restored.symbol == leg.symbol
        assert restored.signal_type == leg.signal_type
        assert restored.quantity == leg.quantity
        assert restored.order_type == leg.order_type
        assert restored.limit_price == leg.limit_price


class TestSignal:
    def test_simple_constructor(self):
        signal = Signal.simple(
            "AAPL", SignalType.BUY, 100, reasoning="Momentum breakout"
        )
        assert len(signal.legs) == 1
        assert signal.legs[0].symbol == "AAPL"
        assert signal.legs[0].signal_type == SignalType.BUY
        assert signal.legs[0].quantity == 100
        assert signal.strategy_type == "single"
        assert signal.reasoning == "Momentum breakout"
        assert signal.net_debit_limit is None
        assert signal.net_credit_limit is None

    def test_simple_crypto(self):
        signal = Signal.simple(
            "BTC/USD", SignalType.BUY, 0.05, asset_type="crypto"
        )
        assert signal.legs[0].asset_type == "crypto"
        assert signal.legs[0].quantity == 0.05

    def test_multi_leg_spread(self):
        signal = Signal(
            legs=[
                SignalLeg("AAPL250620C00200000", SignalType.BUY, 1, asset_type="options"),
                SignalLeg("AAPL250620C00210000", SignalType.SELL, 1, asset_type="options"),
            ],
            strategy_type="bull_call_spread",
            net_debit_limit=3.50,
            reasoning="Bullish into earnings",
        )
        assert len(signal.legs) == 2
        assert signal.strategy_type == "bull_call_spread"
        assert signal.net_debit_limit == 3.50

    def test_pairs_trade(self):
        signal = Signal(
            legs=[
                SignalLeg("BTC/USD", SignalType.BUY, 0.1, asset_type="crypto"),
                SignalLeg("ETH/USD", SignalType.SELL, 2.0, asset_type="crypto"),
            ],
            strategy_type="pairs_trade",
            reasoning="BTC/ETH ratio reverting",
        )
        assert len(signal.legs) == 2
        assert signal.strategy_type == "pairs_trade"

    def test_is_multi_leg(self):
        single = Signal.simple("AAPL", SignalType.BUY, 100)
        assert single.is_multi_leg is False

        multi = Signal(
            legs=[
                SignalLeg("AAPL", SignalType.BUY, 100),
                SignalLeg("MSFT", SignalType.SELL, 50),
            ],
            strategy_type="pairs_trade",
        )
        assert multi.is_multi_leg is True

    def test_serialization_roundtrip(self):
        signal = Signal(
            legs=[
                SignalLeg("AAPL250620C00200000", SignalType.BUY, 1, asset_type="options"),
                SignalLeg("AAPL250620C00210000", SignalType.SELL, 1, asset_type="options"),
            ],
            strategy_type="bull_call_spread",
            net_debit_limit=3.50,
            reasoning="test",
            metadata={"confidence": 0.85},
        )
        d = signal.to_dict()
        restored = Signal.from_dict(d)
        assert len(restored.legs) == 2
        assert restored.legs[0].symbol == "AAPL250620C00200000"
        assert restored.strategy_type == "bull_call_spread"
        assert restored.net_debit_limit == 3.50
        assert restored.reasoning == "test"
        assert restored.metadata == {"confidence": 0.85}
