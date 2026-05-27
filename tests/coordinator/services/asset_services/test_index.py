from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from coordinator.services.asset_services.index import IndexAssetService


@pytest.fixture
def svc():
    return IndexAssetService()


def test_classify_known_indexes(svc):
    assert svc.classify("VIX")
    assert svc.classify("SPX")
    assert svc.classify("NDX")
    assert svc.classify("RUT")
    assert svc.classify("DJI")


def test_classify_polygon_prefix(svc):
    assert svc.classify("I:VIX")
    assert svc.classify("I:SPX")


def test_classify_yfinance_caret(svc):
    assert svc.classify("^GSPC")
    assert svc.classify("^VIX")


def test_classify_rejects_equities(svc):
    assert not svc.classify("AAPL")
    assert not svc.classify("QQQ")


def test_classify_rejects_options(svc):
    assert not svc.classify("SPY241029C00586000")


def test_classify_rejects_crypto(svc):
    assert not svc.classify("BTCUSD")


def test_resolve_symbol_polygon(svc):
    assert svc.resolve_symbol("VIX", "polygon") == "I:VIX"
    assert svc.resolve_symbol("SPX", "polygon") == "I:SPX"
    assert svc.resolve_symbol("NDX", "polygon") == "I:NDX"


def test_resolve_symbol_polygon_idempotent(svc):
    assert svc.resolve_symbol("I:VIX", "polygon") == "I:VIX"


def test_resolve_symbol_yfinance(svc):
    assert svc.resolve_symbol("VIX", "yfinance") == "^VIX"
    assert svc.resolve_symbol("SPX", "yfinance") == "^GSPC"
    assert svc.resolve_symbol("NDX", "yfinance") == "^IXIC"


def test_resolve_symbol_other_passthrough(svc):
    assert svc.resolve_symbol("VIX", "tradier") == "VIX"


def test_compose_order_symbol(svc):
    leg = SimpleNamespace(symbol="VIX")
    assert svc.compose_order_symbol(leg) == "VIX"


def test_multiplier(svc):
    assert svc.get_multiplier() == 1


def test_unrealized_pnl(svc):
    pnl = svc.compute_unrealized_pnl("VIX", quantity=100, avg_price=15.0, market_value=1600.0)
    assert pnl == pytest.approx(100.0)


def test_handle_expiry_returns_none(svc):
    assert svc.handle_expiry("VIX", 1, 20, datetime.now(), None) is None


def test_time_in_force(svc):
    assert svc.time_in_force() == "DAY"


def test_supports_multileg(svc):
    assert svc.supports_multileg() is False


def test_required_order_fields(svc):
    assert svc.required_order_fields() == set()


def test_is_pdt_exempt(svc):
    assert svc.is_pdt_exempt() is False


def test_market_open_weekday(svc):
    ts = datetime(2026, 5, 25, 18, 0, tzinfo=timezone.utc)
    assert svc.is_market_open(ts)


def test_market_closed_weekend(svc):
    ts = datetime(2026, 5, 23, 18, 0, tzinfo=timezone.utc)
    assert not svc.is_market_open(ts)


def test_stream_config_polygon(svc):
    cfg = svc.stream_config("polygon")
    assert cfg.supported
    assert cfg.cluster == "stocks"


def test_stream_config_coinbase_unsupported(svc):
    assert not svc.stream_config("coinbase").supported


def test_supports_provider_polygon(svc):
    assert svc.supports_provider("polygon")


def test_supports_provider_yfinance(svc):
    assert svc.supports_provider("yfinance")


def test_supports_provider_coinbase_no(svc):
    assert not svc.supports_provider("coinbase")


@pytest.mark.asyncio
async def test_discover_returns_underlying(svc):
    result = await svc.discover_contracts("VIX", None, None, {}, None)
    assert result == ["VIX"]
