from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from coordinator.services.asset_services.crypto import CryptoAssetService


@pytest.fixture
def svc():
    return CryptoAssetService()


def test_classify_crypto(svc):
    assert svc.classify("BTCUSD")
    assert svc.classify("ETHUSD")
    assert svc.classify("SOLUSD")
    assert svc.classify("DOGEUSD")
    assert svc.classify("BTCUSDT")


def test_classify_with_slash(svc):
    assert svc.classify("BTC/USD")
    assert svc.classify("ETH/USD")


def test_classify_rejects_equities(svc):
    assert not svc.classify("AAPL")
    assert not svc.classify("SPY")


def test_classify_rejects_options(svc):
    assert not svc.classify("SPY241029C00586000")


def test_classify_rejects_indexes(svc):
    assert not svc.classify("VIX")


def test_resolve_symbol_yfinance(svc):
    assert svc.resolve_symbol("BTCUSD", "yfinance") == "BTC-USD"
    assert svc.resolve_symbol("ETHUSD", "yfinance") == "ETH-USD"
    assert svc.resolve_symbol("SOLUSD", "yfinance") == "SOL-USD"


def test_resolve_symbol_polygon(svc):
    assert svc.resolve_symbol("BTCUSD", "polygon") == "BTCUSD"


def test_resolve_symbol_alpaca_stream_uses_slash(svc):
    assert svc.resolve_symbol("BTCUSD", "alpaca_stream") == "BTC/USD"


def test_resolve_symbol_coinbase_dash(svc):
    assert svc.resolve_symbol("BTCUSD", "coinbase") == "BTC-USD"


def test_compose_order_symbol(svc):
    leg = SimpleNamespace(symbol="BTCUSD")
    assert svc.compose_order_symbol(leg) == "BTCUSD"


def test_multiplier(svc):
    assert svc.get_multiplier() == 1


def test_get_price_from_bars(svc):
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-05-22"]),
        "close": [50000.0],
    })
    ctx = SimpleNamespace(_bars={("polygon", "BTCUSD", "1day"): df})
    assert svc.get_price("BTCUSD", datetime(2026, 5, 22, 12), ctx) == 50000.0


def test_unrealized_pnl(svc):
    pnl = svc.compute_unrealized_pnl("BTCUSD", quantity=0.5, avg_price=40000.0, market_value=25000.0)
    assert pnl == pytest.approx(5000.0)


def test_handle_expiry_returns_none(svc):
    assert svc.handle_expiry("BTCUSD", 1, 50000, datetime.now(), None) is None


def test_time_in_force_gtc(svc):
    assert svc.time_in_force() == "GTC"


def test_supports_multileg(svc):
    assert svc.supports_multileg() is False


def test_required_order_fields(svc):
    assert svc.required_order_fields() == set()


def test_is_pdt_exempt(svc):
    assert svc.is_pdt_exempt() is True


def test_market_always_open_saturday(svc):
    ts = datetime(2026, 5, 23, 3, 0, tzinfo=timezone.utc)
    assert svc.is_market_open(ts)


def test_market_always_open_overnight(svc):
    ts = datetime(2026, 5, 25, 3, 0, tzinfo=timezone.utc)
    assert svc.is_market_open(ts)


def test_market_always_open_midweek(svc):
    ts = datetime(2026, 5, 25, 15, 0, tzinfo=timezone.utc)
    assert svc.is_market_open(ts)


def test_stream_config_alpaca(svc):
    cfg = svc.stream_config("alpaca")
    assert cfg.supported
    assert cfg.stream_class == "crypto"
    assert cfg.symbol_transform == "crypto_slash"


def test_stream_config_coinbase(svc):
    cfg = svc.stream_config("coinbase")
    assert cfg.supported
    assert cfg.symbol_transform == "crypto_dash"


def test_stream_config_polygon(svc):
    cfg = svc.stream_config("polygon")
    assert cfg.supported
    assert cfg.cluster == "crypto"
    assert cfg.symbol_transform == "polygon_x_prefix"


def test_stream_config_tradier_unsupported(svc):
    assert not svc.stream_config("tradier").supported


def test_supports_provider_coinbase(svc):
    assert svc.supports_provider("coinbase")


def test_supports_provider_alpaca(svc):
    assert svc.supports_provider("alpaca")


def test_supports_provider_tradier_no(svc):
    assert not svc.supports_provider("tradier")


@pytest.mark.asyncio
async def test_discover_returns_underlying(svc):
    result = await svc.discover_contracts("BTCUSD", None, None, {}, None)
    assert result == ["BTCUSD"]
