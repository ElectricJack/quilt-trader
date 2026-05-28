import numpy as np
import pandas as pd

from coordinator.services.validation.regime import tag_regimes, regime_conditional_metrics


def test_tag_regimes_basic():
    idx = pd.date_range("2024-01-01", periods=300, freq="D")
    # Step function: first 100 days flat, next 100 days +25%, next 100 days -25%
    arr = np.concatenate([
        np.ones(100),
        np.linspace(1.0, 1.25, 100),
        np.linspace(1.25, 0.94, 100),
    ])
    prices = pd.Series(arr, index=idx)
    regimes = tag_regimes(prices, lookback_days=90, bull_threshold=0.15, bear_threshold=-0.15)
    assert (regimes.iloc[10] == "chop") or pd.isna(regimes.iloc[10])  # warmup
    # By day 195 the trailing-90 return is well above +15%
    assert regimes.iloc[195] == "bull"
    # By day 290 the trailing-90 return is well below -15%
    assert regimes.iloc[290] == "bear"


def test_regime_conditional_metrics():
    idx = pd.date_range("2024-01-01", periods=200, freq="D")
    equity = pd.Series(np.linspace(1000.0, 1200.0, 200), index=idx)
    regimes = pd.Series(["bull"] * 100 + ["chop"] * 100, index=idx)
    out = regime_conditional_metrics(equity, regimes)
    assert set(out.keys()) >= {"bull", "chop"}
    assert "sharpe" in out["bull"]
    assert "total_return" in out["bull"]


# ---- VIX-based tagger ----

def test_tag_regimes_by_vix_three_buckets():
    from coordinator.services.validation.regime import tag_regimes_by_vix
    idx = pd.date_range("2024-01-01", periods=300, freq="D")
    # Linearly increasing VIX from 10 to 40
    vix = pd.Series(np.linspace(10, 40, 300), index=idx)
    tags = tag_regimes_by_vix(vix, low_pctile=0.33, high_pctile=0.67)
    # First third → low_vol, middle → mid_vol, last third → high_vol
    assert tags.iloc[0] == "low_vol"
    assert tags.iloc[150] == "mid_vol"
    assert tags.iloc[-1] == "high_vol"
    # All three buckets present
    assert set(tags.unique()) == {"low_vol", "mid_vol", "high_vol"}


def test_tag_regimes_by_vix_empty_input():
    from coordinator.services.validation.regime import tag_regimes_by_vix
    out = tag_regimes_by_vix(pd.Series(dtype=float))
    assert out.empty


# ---- FunctionTagger ----

def test_function_tagger_wraps_arbitrary_callable():
    from coordinator.services.validation.regime import FunctionTagger
    idx = pd.date_range("2024-01-01", periods=10, freq="D")
    series = pd.Series(range(10), index=idx)

    def odd_even(s):
        return pd.Series(["odd" if v % 2 else "even" for v in s], index=s.index)

    tagger = FunctionTagger(odd_even, name="oddeven")
    out = tagger(series)
    assert tagger.name == "oddeven"
    assert out.iloc[0] == "even"
    assert out.iloc[1] == "odd"


def test_function_tagger_rejects_non_series_return():
    import pytest
    from coordinator.services.validation.regime import FunctionTagger
    bad = FunctionTagger(lambda s: ["a"] * len(s), name="bad")
    with pytest.raises(TypeError, match="expected pd.Series"):
        bad(pd.Series([1, 2, 3]))


# ---- Backward-compat alias ----

def test_tag_regimes_alias_still_works():
    """Pre-existing callers using tag_regimes() (singular) keep working."""
    from coordinator.services.validation.regime import tag_regimes, tag_regimes_by_trailing_return
    idx = pd.date_range("2024-01-01", periods=100, freq="D")
    series = pd.Series(np.linspace(1.0, 1.5, 100), index=idx)
    out_a = tag_regimes(series, lookback_days=30)
    out_b = tag_regimes_by_trailing_return(series, lookback_days=30)
    pd.testing.assert_series_equal(out_a, out_b)
