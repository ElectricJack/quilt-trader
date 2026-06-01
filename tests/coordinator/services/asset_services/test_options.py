from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from coordinator.services.asset_services.options import OptionsAssetService


@pytest.fixture
def svc():
    return OptionsAssetService()


def test_classify_occ_with_o_prefix(svc):
    # O: prefix is not canonical OCC — classify() rejects it
    assert not svc.classify("O:SPY241029C00586000")


def test_classify_occ_without_prefix(svc):
    assert svc.classify("SPY241029C00586000")
    assert svc.classify("QQQ260417P00637000")


def test_classify_rejects_equities(svc):
    assert not svc.classify("AAPL")
    assert not svc.classify("SPY")


def test_classify_rejects_crypto(svc):
    assert not svc.classify("BTCUSD")


def test_classify_rejects_indexes(svc):
    assert not svc.classify("VIX")


def test_parse_symbol(svc):
    p = svc.parse_symbol("SPY241029C00586000")
    assert p["underlying"] == "SPY"
    assert p["expiration"] == "2024-10-29"
    assert p["option_type"] == "call"
    assert p["strike"] == 586.0


def test_parse_symbol_with_prefix(svc):
    p = svc.parse_symbol("O:QQQ260320C00580000")
    assert p["underlying"] == "QQQ"
    assert p["strike"] == 580.0


def test_parse_symbol_returns_none_for_invalid(svc):
    assert svc.parse_symbol("AAPL") is None


def test_resolve_symbol_polygon_adds_prefix(svc):
    assert svc.resolve_symbol("SPY241029C00586000", "polygon") == "O:SPY241029C00586000"


def test_resolve_symbol_polygon_already_prefixed_raises(svc):
    # resolve_symbol() requires canonical (no O: prefix) input
    with pytest.raises(ValueError, match="not a canonical option"):
        svc.resolve_symbol("O:SPY241029C00586000", "polygon")


def test_resolve_symbol_tradier_strips_prefix_raises(svc):
    # resolve_symbol() requires canonical input; use canonicalize() to strip O: first
    with pytest.raises(ValueError, match="not a canonical option"):
        svc.resolve_symbol("O:SPY241029C00586000", "tradier")


def test_resolve_symbol_tradier_no_prefix_passthrough(svc):
    assert svc.resolve_symbol("SPY241029C00586000", "tradier") == "SPY241029C00586000"


def test_compose_order_symbol_from_leg(svc):
    leg = SimpleNamespace(
        symbol="SPY",
        asset_type="options",
        expiry="2024-10-29",
        strike=586.0,
        right="call",
    )
    assert svc.compose_order_symbol(leg) == "SPY241029C00586000"


def test_compose_order_symbol_put(svc):
    leg = SimpleNamespace(
        symbol="QQQ",
        asset_type="options",
        expiry="2026-04-17",
        strike=637.0,
        right="put",
    )
    assert svc.compose_order_symbol(leg) == "QQQ260417P00637000"


def test_compose_order_symbol_missing_fields_raises(svc):
    leg = SimpleNamespace(symbol="SPY", asset_type="options", expiry=None, strike=None, right=None)
    with pytest.raises(ValueError):
        svc.compose_order_symbol(leg)


def test_multiplier(svc):
    assert svc.get_multiplier() == 100


def test_get_price_uses_data_service(svc):
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-05-22"]),
        "close": [5.5],
    })
    ds = MagicMock()
    ds.load_market_data = MagicMock(return_value=df)
    ctx = SimpleNamespace(_data_service=ds, _default_source="polygon")
    price = svc.get_price("O:SPY241029C00586000", datetime(2026, 5, 22, 12), ctx)
    assert price == 5.5
    ds.load_market_data.assert_called_with("polygon", "SPY241029C00586000", "1day")


def test_get_price_returns_none_when_no_ctx(svc):
    assert svc.get_price("SPY241029C00586000", datetime(2026, 5, 22), None) is None


def test_get_fill_price_buy_uses_ask(svc):
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-05-22"]),
        "close": [5.5], "bid": [5.4], "ask": [5.6], "volume": [100],
    })
    ds = MagicMock()
    ds.load_market_data = MagicMock(return_value=df)
    ctx = SimpleNamespace(_data_service=ds, _default_source="polygon")
    price = svc.get_fill_price("SPY241029C00586000", "buy", datetime(2026, 5, 22, 12), ctx)
    assert price == 5.6


def test_get_fill_price_sell_uses_bid(svc):
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-05-22"]),
        "close": [5.5], "bid": [5.4], "ask": [5.6], "volume": [100],
    })
    ds = MagicMock()
    ds.load_market_data = MagicMock(return_value=df)
    ctx = SimpleNamespace(_data_service=ds, _default_source="polygon")
    price = svc.get_fill_price("SPY241029C00586000", "sell", datetime(2026, 5, 22, 12), ctx)
    assert price == 5.4


def test_get_fill_price_falls_back_to_spread_estimate(svc):
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-05-22"]),
        "close": [5.0], "volume": [100],
    })
    ds = MagicMock()
    ds.load_market_data = MagicMock(return_value=df)
    ctx = SimpleNamespace(_data_service=ds, _default_source="polygon")
    buy = svc.get_fill_price("SPY241029C00586000", "buy", datetime(2026, 5, 22, 12), ctx)
    sell = svc.get_fill_price("SPY241029C00586000", "sell", datetime(2026, 5, 22, 12), ctx)
    assert buy > 5.0
    assert sell < 5.0


def test_unrealized_pnl_with_multiplier(svc):
    pnl = svc.compute_unrealized_pnl(
        "SPY241029C00586000", quantity=5, avg_price=10.0, market_value=6000.0,
    )
    assert pnl == pytest.approx(1000.0)


def test_unrealized_pnl_zero_market_value(svc):
    pnl = svc.compute_unrealized_pnl(
        "SPY241029C00586000", quantity=5, avg_price=10.0, market_value=0.0,
    )
    assert pnl == 0.0


def test_risk_contribution_delta_adjusted(svc):
    ds = MagicMock()
    ds.load_market_data = MagicMock(return_value=None)
    risk = svc.risk_contribution("SPY241029C00586000", market_value=10000.0, data_service=ds)
    assert 0 < risk < 200.0


def test_handle_expiry_not_expired(svc):
    result = svc.handle_expiry(
        "SPY260110C00600000",
        quantity=1, avg_price=5.0,
        sim_time=datetime(2026, 1, 9), ctx=None,
    )
    assert result is None


def test_handle_expiry_long_call_itm(svc):
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-01-10"]),
        "close": [650.0],
    })
    ctx = SimpleNamespace(_bars={("polygon", "SPY", "1day"): df})
    result = svc.handle_expiry(
        "SPY260110C00600000",
        quantity=1, avg_price=5.0,
        sim_time=datetime(2026, 1, 11), ctx=ctx,
    )
    assert result is not None
    assert result.fill_price == 50.0
    assert result.realized_pnl == pytest.approx(4500.0)
    assert result.side == "sell"


def test_handle_expiry_short_call_otm(svc):
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-01-10"]),
        "close": [500.0],
    })
    ctx = SimpleNamespace(_bars={("polygon", "SPY", "1day"): df})
    result = svc.handle_expiry(
        "SPY260110C00600000",
        quantity=-1, avg_price=5.0,
        sim_time=datetime(2026, 1, 11), ctx=ctx,
    )
    assert result is not None
    assert result.fill_price == 0.0
    assert result.realized_pnl == pytest.approx(500.0)
    assert result.side == "buy"


def test_handle_expiry_put_itm(svc):
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-01-10"]),
        "close": [550.0],
    })
    ctx = SimpleNamespace(_bars={("polygon", "SPY", "1day"): df})
    result = svc.handle_expiry(
        "SPY260110P00600000",
        quantity=1, avg_price=5.0,
        sim_time=datetime(2026, 1, 11), ctx=ctx,
    )
    assert result is not None
    assert result.fill_price == 50.0
    assert result.realized_pnl == pytest.approx(4500.0)


def test_time_in_force(svc):
    assert svc.time_in_force() == "DAY"


def test_supports_multileg(svc):
    assert svc.supports_multileg() is True


def test_required_order_fields(svc):
    assert svc.required_order_fields() == {"expiry", "strike", "right"}


def test_is_pdt_exempt(svc):
    assert svc.is_pdt_exempt() is False


def test_market_hours_follows_equity(svc):
    from datetime import timezone as tz
    assert svc.is_market_open(datetime(2026, 5, 25, 18, 0, tzinfo=tz.utc))
    assert not svc.is_market_open(datetime(2026, 5, 23, 18, 0, tzinfo=tz.utc))


def test_stream_config_polygon_options(svc):
    cfg = svc.stream_config("polygon")
    assert cfg.supported
    assert cfg.cluster == "options"
    assert cfg.symbol_transform == "occ_prefix"


def test_stream_config_alpaca_options(svc):
    cfg = svc.stream_config("alpaca")
    assert cfg.supported


def test_stream_config_coinbase_no(svc):
    assert not svc.stream_config("coinbase").supported


def test_supports_provider_coinbase_no(svc):
    assert not svc.supports_provider("coinbase")


@pytest.mark.asyncio
async def test_discover_contracts_calls_provider(svc):
    provider = MagicMock()
    provider.discover_option_contracts = AsyncMock(return_value=[
        {"ticker": "O:SPY260117C00450000"},
        {"ticker": "O:SPY260117C00455000"},
    ])
    result = await svc.discover_contracts(
        "SPY", date(2026, 1, 1), date(2026, 1, 17),
        {"strike_range": "atm5", "max_contracts_per_exp": 60, "underlying_price": 450.0},
        provider,
    )
    assert result == ["SPY260117C00450000", "SPY260117C00455000"]
    provider.discover_option_contracts.assert_awaited_once()


@pytest.mark.asyncio
async def test_discover_contracts_no_provider(svc):
    result = await svc.discover_contracts("SPY", None, None, {}, None)
    assert result == []
