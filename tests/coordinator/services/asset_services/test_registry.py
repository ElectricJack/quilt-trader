from types import SimpleNamespace

import pytest

from coordinator.services.asset_services.base import AssetType
from coordinator.services.asset_services.registry import (
    AssetServiceRegistry,
    get_default_registry,
)


@pytest.fixture
def registry():
    return AssetServiceRegistry()


def test_classify_equities(registry):
    assert registry.classify("AAPL") == AssetType.EQUITIES
    assert registry.classify("SPY") == AssetType.EQUITIES
    assert registry.classify("QQQ") == AssetType.EQUITIES


def test_classify_options(registry):
    assert registry.classify("SPY241029C00586000") == AssetType.OPTIONS
    assert registry.classify("O:QQQ260320C00580000") == AssetType.OPTIONS


def test_classify_crypto(registry):
    assert registry.classify("BTCUSD") == AssetType.CRYPTO
    assert registry.classify("ETHUSD") == AssetType.CRYPTO


def test_classify_indexes(registry):
    assert registry.classify("VIX") == AssetType.INDEX
    assert registry.classify("I:SPX") == AssetType.INDEX


def test_classify_unknown_defaults_to_equities(registry):
    assert registry.classify("UNKNOWN") == AssetType.EQUITIES


def test_classify_empty_string(registry):
    assert registry.classify("") == AssetType.EQUITIES


def test_options_checked_before_equities(registry):
    svc = registry.get_service("SPY241029C00586000")
    assert svc.asset_type == AssetType.OPTIONS


def test_indexes_checked_before_equities(registry):
    svc = registry.get_service("VIX")
    assert svc.asset_type == AssetType.INDEX


def test_crypto_checked_before_equities(registry):
    svc = registry.get_service("BTCUSD")
    assert svc.asset_type == AssetType.CRYPTO


def test_resolve_symbol_delegates(registry):
    assert registry.resolve_symbol("VIX", "polygon") == "I:VIX"
    assert registry.resolve_symbol("SPY241029C00586000", "polygon") == "O:SPY241029C00586000"
    assert registry.resolve_symbol("BTCUSD", "yfinance") == "BTC-USD"
    assert registry.resolve_symbol("AAPL", "polygon") == "AAPL"


def test_get_multiplier_delegates(registry):
    assert registry.get_multiplier("AAPL") == 1
    assert registry.get_multiplier("SPY241029C00586000") == 100
    assert registry.get_multiplier("BTCUSD") == 1
    assert registry.get_multiplier("VIX") == 1


def test_time_in_force_delegates(registry):
    assert registry.time_in_force("AAPL") == "DAY"
    assert registry.time_in_force("BTCUSD") == "GTC"
    assert registry.time_in_force("SPY241029C00586000") == "DAY"


def test_is_market_open_delegates(registry):
    from datetime import datetime, timezone
    sat = datetime(2026, 5, 23, 18, 0, tzinfo=timezone.utc)
    assert registry.is_market_open("BTCUSD", sat) is True
    assert registry.is_market_open("AAPL", sat) is False


def test_compose_order_symbol_delegates(registry):
    leg = SimpleNamespace(symbol="AAPL")
    assert registry.compose_order_symbol(leg) == "AAPL"
    opt_leg = SimpleNamespace(
        symbol="SPY", asset_type="options",
        expiry="2024-10-29", strike=586.0, right="call",
    )
    assert registry.compose_order_symbol(opt_leg) == "SPY241029C00586000"


def test_supports_provider_delegates(registry):
    assert registry.supports_provider("BTCUSD", "coinbase")
    assert not registry.supports_provider("AAPL", "coinbase")


def test_get_service_by_type(registry):
    assert registry.get_service_by_type(AssetType.OPTIONS).asset_type == AssetType.OPTIONS
    assert registry.get_service_by_type("crypto").asset_type == AssetType.CRYPTO
    assert registry.get_service_by_type("equities").asset_type == AssetType.EQUITIES


def test_default_registry_returns_same_instance():
    r1 = get_default_registry()
    r2 = get_default_registry()
    assert r1 is r2
