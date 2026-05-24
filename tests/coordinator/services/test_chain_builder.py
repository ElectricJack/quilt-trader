import pandas as pd
import pytest
from coordinator.services.chain_builder import build_chain_from_bars, parse_occ_symbol


def test_parse_occ_symbol_with_prefix():
    result = parse_occ_symbol("O:SPY241029C00586000")
    assert result == {
        "underlying": "SPY",
        "expiration": "2024-10-29",
        "option_type": "call",
        "strike": 586.0,
        "raw_symbol": "SPY241029C00586000",
    }


def test_parse_occ_symbol_without_prefix():
    result = parse_occ_symbol("SPY241029P00570000")
    assert result == {
        "underlying": "SPY",
        "expiration": "2024-10-29",
        "option_type": "put",
        "strike": 570.0,
        "raw_symbol": "SPY241029P00570000",
    }


def test_parse_occ_symbol_invalid():
    assert parse_occ_symbol("SPY") is None
    assert parse_occ_symbol("AAPL") is None


def test_build_chain_from_bars_basic():
    bars = {
        "SPY241029C00580000": pd.DataFrame({
            "timestamp": pd.to_datetime(["2024-10-25", "2024-10-28"]),
            "open": [6.0, 5.5], "high": [6.5, 6.0],
            "low": [5.5, 5.0], "close": [6.2, 5.8],
            "volume": [100, 150],
        }),
        "SPY241029P00580000": pd.DataFrame({
            "timestamp": pd.to_datetime(["2024-10-25", "2024-10-28"]),
            "open": [4.0, 4.5], "high": [4.5, 5.0],
            "low": [3.5, 4.0], "close": [4.2, 4.8],
            "volume": [80, 120],
        }),
    }
    chain = build_chain_from_bars(bars, as_of=pd.Timestamp("2024-10-28"))
    assert len(chain) == 2
    assert set(chain.columns) >= {
        "symbol", "strike", "option_type", "bid", "ask",
        "last", "volume", "open_interest", "implied_volatility",
    }
    call_row = chain[chain["option_type"] == "call"].iloc[0]
    assert call_row["strike"] == 580.0
    assert call_row["last"] == pytest.approx(5.8)
    assert call_row["symbol"] == "SPY241029C00580000"


def test_build_chain_from_bars_filters_by_as_of():
    """Only bars at or before as_of should be used."""
    bars = {
        "SPY241029C00580000": pd.DataFrame({
            "timestamp": pd.to_datetime(["2024-10-25", "2024-10-28", "2024-10-29"]),
            "open": [6.0, 5.5, 7.0], "high": [6.5, 6.0, 7.5],
            "low": [5.5, 5.0, 6.5], "close": [6.2, 5.8, 7.2],
            "volume": [100, 150, 200],
        }),
    }
    chain = build_chain_from_bars(bars, as_of=pd.Timestamp("2024-10-28"))
    assert len(chain) == 1
    assert chain.iloc[0]["last"] == pytest.approx(5.8)


def test_build_chain_from_bars_empty():
    chain = build_chain_from_bars({}, as_of=pd.Timestamp("2024-10-28"))
    assert chain.empty
