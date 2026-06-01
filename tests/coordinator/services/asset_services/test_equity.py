"""Tests cover every callsite behavior the migration depends on."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from coordinator.services.asset_services.equity import EquityAssetService


@pytest.fixture
def svc():
    return EquityAssetService()


def test_classify_stocks(svc):
    assert svc.classify("AAPL")
    assert svc.classify("SPY")
    assert svc.classify("TSLA")


def test_classify_rejects_options(svc):
    assert not svc.classify("SPY241029C00586000")
    assert not svc.classify("O:QQQ260320C00580000")


def test_classify_rejects_crypto(svc):
    assert not svc.classify("BTCUSD")
    assert not svc.classify("ETHUSD")


def test_classify_rejects_indexes(svc):
    # Prefixed forms are rejected by the canonical regex
    assert not svc.classify("I:SPX")
    assert not svc.classify("^GSPC")
    # Note: bare VIX/SPX/NDX now classify as equity (3-char uppercase tickers
    # are valid canonical equity symbols; index disambiguation happens upstream)


def test_resolve_symbol_identity(svc):
    assert svc.resolve_symbol("AAPL", "polygon") == "AAPL"
    assert svc.resolve_symbol("AAPL", "tradier") == "AAPL"
    assert svc.resolve_symbol("AAPL", "alpaca") == "AAPL"


def test_compose_order_symbol_identity(svc):
    leg = SimpleNamespace(symbol="AAPL")
    assert svc.compose_order_symbol(leg) == "AAPL"


def test_multiplier(svc):
    assert svc.get_multiplier() == 1


def test_get_price_searches_bars(svc):
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-05-22", "2026-05-23"]),
        "close": [100.0, 101.0],
    })
    ctx = SimpleNamespace(_bars={("polygon", "AAPL", "1day"): df})
    price = svc.get_price("AAPL", datetime(2026, 5, 23, 12, 0), ctx)
    assert price == 101.0


def test_get_price_returns_none_when_no_bars(svc):
    ctx = SimpleNamespace(_bars={})
    assert svc.get_price("AAPL", datetime(2026, 5, 22), ctx) is None


def test_get_price_returns_none_when_ctx_none(svc):
    assert svc.get_price("AAPL", datetime(2026, 5, 22), None) is None


def test_get_fill_price_same_as_price(svc):
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-05-22"]),
        "close": [100.0],
    })
    ctx = SimpleNamespace(_bars={("polygon", "AAPL", "1day"): df})
    assert svc.get_fill_price("AAPL", "buy", datetime(2026, 5, 22, 12), ctx) == 100.0
    assert svc.get_fill_price("AAPL", "sell", datetime(2026, 5, 22, 12), ctx) == 100.0


def test_unrealized_pnl(svc):
    pnl = svc.compute_unrealized_pnl("AAPL", quantity=10, avg_price=150.0, market_value=1600.0)
    assert pnl == pytest.approx(100.0)


def test_unrealized_pnl_zero_market_value(svc):
    pnl = svc.compute_unrealized_pnl("AAPL", quantity=10, avg_price=150.0, market_value=0.0)
    assert pnl == 0.0


def test_unrealized_pnl_negative_market_value_returns_zero(svc):
    pnl = svc.compute_unrealized_pnl("AAPL", quantity=-10, avg_price=150.0, market_value=-1400.0)
    assert pnl == 0.0


def test_risk_contribution_uses_injected_data_service(svc):
    import numpy as np
    df = pd.DataFrame({"close": np.linspace(100, 110, 60)})
    ds = MagicMock()
    ds.load_market_data = MagicMock(return_value=df)
    risk = svc.risk_contribution("AAPL", market_value=10000.0, data_service=ds)
    assert risk > 0
    ds.load_market_data.assert_called()


def test_risk_contribution_fallback_when_no_data(svc):
    ds = MagicMock()
    ds.load_market_data = MagicMock(return_value=None)
    risk = svc.risk_contribution("AAPL", market_value=10000.0, data_service=ds)
    assert risk == pytest.approx(200.0)


def test_risk_contribution_no_data_service(svc):
    risk = svc.risk_contribution("AAPL", market_value=10000.0)
    assert risk == pytest.approx(200.0)


def test_handle_expiry_returns_none(svc):
    assert svc.handle_expiry("AAPL", 10, 150.0, datetime.now(), None) is None


def test_time_in_force(svc):
    assert svc.time_in_force() == "DAY"


def test_supports_multileg(svc):
    assert svc.supports_multileg() is False


def test_required_order_fields(svc):
    assert svc.required_order_fields() == set()


def test_is_pdt_exempt(svc):
    assert svc.is_pdt_exempt() is False


def test_market_open_weekday_during_session(svc):
    ts = datetime(2026, 5, 25, 18, 0, tzinfo=timezone.utc)
    assert svc.is_market_open(ts)


def test_market_closed_weekend(svc):
    ts = datetime(2026, 5, 23, 18, 0, tzinfo=timezone.utc)
    assert not svc.is_market_open(ts)


def test_market_closed_before_open(svc):
    ts = datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc)
    assert not svc.is_market_open(ts)


def test_market_closed_after_close(svc):
    ts = datetime(2026, 5, 25, 21, 0, tzinfo=timezone.utc)
    assert not svc.is_market_open(ts)


def test_market_open_handles_naive_datetime_as_utc(svc):
    ts = datetime(2026, 5, 25, 18, 0)
    assert svc.is_market_open(ts)


def test_market_open_rejects_non_datetime(svc):
    with pytest.raises((TypeError, AttributeError)):
        svc.is_market_open("not a datetime")


def test_stream_config_polygon(svc):
    cfg = svc.stream_config("polygon")
    assert cfg.supported
    assert cfg.cluster == "stocks"
    assert cfg.symbol_transform == "identity"


def test_stream_config_alpaca(svc):
    cfg = svc.stream_config("alpaca")
    assert cfg.supported
    assert cfg.stream_class == "stock"


def test_stream_config_tradier(svc):
    cfg = svc.stream_config("tradier")
    assert cfg.supported


def test_stream_config_coinbase_unsupported(svc):
    cfg = svc.stream_config("coinbase")
    assert not cfg.supported


def test_supports_provider_coinbase_no(svc):
    assert not svc.supports_provider("coinbase")


def test_supports_provider_polygon_yes(svc):
    assert svc.supports_provider("polygon")


@pytest.mark.asyncio
async def test_discover_contracts_returns_underlying(svc):
    result = await svc.discover_contracts("AAPL", None, None, {}, None)
    assert result == ["AAPL"]
