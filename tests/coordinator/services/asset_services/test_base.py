"""Tests for protocol + StreamConfig + _bar_lookup helper."""
from datetime import datetime, timezone

import pandas as pd

from coordinator.services.asset_services.base import (
    AssetType,
    Settlement,
    StreamConfig,
    _bar_lookup,
)


def test_asset_type_values():
    assert AssetType.EQUITIES.value == "equities"
    assert AssetType.OPTIONS.value == "options"
    assert AssetType.CRYPTO.value == "crypto"
    assert AssetType.INDEX.value == "index"


def test_asset_type_string_subclass():
    assert AssetType.EQUITIES == "equities"
    assert isinstance(AssetType.EQUITIES, str)


def test_settlement_construction():
    s = Settlement(
        symbol="SPY241029C00586000",
        side="sell",
        quantity=5,
        fill_price=14.0,
        realized_pnl=2000.0,
    )
    assert s.symbol == "SPY241029C00586000"
    assert s.realized_pnl == 2000.0


def test_stream_config_construction():
    cfg = StreamConfig(
        supported=True,
        stream_class="stock",
        symbol_transform="identity",
        cap=30,
        cluster="stocks",
    )
    assert cfg.supported is True
    assert cfg.cap == 30
    assert cfg.cluster == "stocks"


def test_bar_lookup_finds_last_bar_before_sim_time():
    df = pd.DataFrame({
        "timestamp": pd.to_datetime([
            "2026-05-20", "2026-05-21", "2026-05-22", "2026-05-23",
        ]),
        "close": [100.0, 101.0, 102.0, 103.0],
    })
    price = _bar_lookup(df, datetime(2026, 5, 22, 23, 59))
    assert price == 102.0


def test_bar_lookup_returns_none_when_no_bars_before():
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-05-23"]),
        "close": [103.0],
    })
    price = _bar_lookup(df, datetime(2026, 5, 22))
    assert price is None


def test_bar_lookup_handles_tz_aware_sim_time():
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-05-22"]),
        "close": [102.0],
    })
    price = _bar_lookup(df, datetime(2026, 5, 23, tzinfo=timezone.utc))
    assert price == 102.0


def test_bar_lookup_handles_tz_aware_timestamps():
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-05-22"], utc=True),
        "close": [102.0],
    })
    price = _bar_lookup(df, datetime(2026, 5, 23))
    assert price == 102.0


def test_bar_lookup_returns_none_on_empty_df():
    df = pd.DataFrame({"timestamp": [], "close": []})
    assert _bar_lookup(df, datetime(2026, 5, 22)) is None
