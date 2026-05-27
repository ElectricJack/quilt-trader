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
