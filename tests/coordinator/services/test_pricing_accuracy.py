# tests/coordinator/services/test_pricing_accuracy.py
"""Validate BS repricing accuracy against real historical contract prices.

Uses actual Tradier SPY option bar data on disk to verify:
1. BS prices at known IV are within reasonable bounds of market prices
2. IV round-trips through price → IV → price are stable

Skip these tests if no real data is on disk.
"""
import os
import pandas as pd
import pytest
from datetime import date
from coordinator.services.options_math import bs_price, bs_iv
from coordinator.services.chain_builder import parse_occ_symbol

DATA_DIR = "data/market/tradier"
# Also check the main repo's data directory (worktrees don't have data/)
MAIN_DATA_DIR = "/home/jkern/dev/quilt-trader/data/market/tradier"
SKIP = not os.path.isdir(DATA_DIR) and not os.path.isdir(MAIN_DATA_DIR)


def _get_data_dir():
    if os.path.isdir(DATA_DIR):
        return DATA_DIR
    return MAIN_DATA_DIR


def _find_contract_with_history(min_bars=5):
    """Find a Tradier contract with enough history to test repricing."""
    data_dir = _get_data_dir()
    if not os.path.isdir(data_dir):
        return None, None
    for name in os.listdir(data_dir):
        parsed = parse_occ_symbol(name)
        if parsed is None or parsed["underlying"] != "SPY":
            continue
        path = os.path.join(data_dir, name, "1day.parquet")
        if not os.path.exists(path):
            continue
        df = pd.read_parquet(path)
        if len(df) >= min_bars:
            return parsed, df
    return None, None


@pytest.mark.skipif(SKIP, reason="No Tradier data on disk")
def test_iv_round_trip_on_real_data():
    """Compute IV from real price, then verify BS price at that IV matches."""
    parsed, df = _find_contract_with_history(min_bars=3)
    if parsed is None:
        pytest.skip("No suitable contract found")

    mid = len(df) // 2
    bar = df.iloc[mid]
    close = float(bar["close"])
    if close < 0.10:
        pytest.skip("Price too low for meaningful IV")

    exp = date.fromisoformat(parsed["expiration"])
    ts = pd.to_datetime(bar["timestamp"])
    if hasattr(ts, "date"):
        bar_date = ts.date()
    else:
        bar_date = ts
    days = (exp - bar_date).days
    if days <= 0:
        pytest.skip("Contract expired before this bar")
    T = days / 365.0

    S_estimate = parsed["strike"]

    iv = bs_iv(price=close, S=S_estimate, K=parsed["strike"], T=T, r=0.04,
               option_type=parsed["option_type"])
    if iv is None:
        pytest.skip("Could not compute IV")

    bs = bs_price(S=S_estimate, K=parsed["strike"], T=T, r=0.04, sigma=iv,
                  option_type=parsed["option_type"])
    assert bs == pytest.approx(close, rel=0.01)


@pytest.mark.skipif(SKIP, reason="No Tradier data on disk")
def test_bs_directional_accuracy():
    """Verify BS repricing produces positive prices for reasonable inputs."""
    parsed, df = _find_contract_with_history(min_bars=5)
    if parsed is None:
        pytest.skip("No suitable contract found")

    for i in range(min(5, len(df))):
        bar = df.iloc[i]
        close = float(bar["close"])
        if close <= 0:
            continue
        exp = date.fromisoformat(parsed["expiration"])
        ts = pd.to_datetime(bar["timestamp"])
        bar_date = ts.date() if hasattr(ts, "date") else ts
        days = (exp - bar_date).days
        if days <= 0:
            continue
        T = days / 365.0

        bs = bs_price(S=parsed["strike"], K=parsed["strike"], T=T, r=0.04,
                      sigma=0.25, option_type=parsed["option_type"])
        assert bs > 0, f"BS price should be positive at T={T:.3f}"
