"""Tests for the conservative options MTM helper."""
from __future__ import annotations

import math
from datetime import date, datetime, timezone

import pytest

from coordinator.services.options_mtm import (
    FALLBACK_SIGMA,
    RISK_FREE_RATE,
    OptionsMTMHelper,
    _IVCacheEntry,
    _MidCacheEntry,
    black_scholes_price,
)


def test_constants_have_expected_values():
    assert RISK_FREE_RATE == 0.045
    assert FALLBACK_SIGMA == 0.40


def test_iv_cache_entry_holds_sim_time_and_iv():
    entry = _IVCacheEntry(
        sim_time=datetime(2024, 1, 1, tzinfo=timezone.utc), iv=0.25
    )
    assert entry.iv == 0.25
    assert entry.sim_time.year == 2024


def test_mid_cache_entry_holds_sim_time_and_mid():
    entry = _MidCacheEntry(
        sim_time=datetime(2024, 1, 1, tzinfo=timezone.utc), mid=1.23
    )
    assert entry.mid == pytest.approx(1.23)


def test_bs_atm_call_one_year_textbook_value():
    # ATM call, S=100, K=100, T=1, r=0.05, sigma=0.20
    # Textbook value ≈ 10.4506 (Hull, Options Futures, Table 13.1)
    price = black_scholes_price(
        S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.20, option_type="call"
    )
    assert price == pytest.approx(10.4506, rel=0.001)


def test_bs_atm_put_one_year_textbook_value():
    # Same ATM, put. Put-call parity: P = C - S + K*exp(-rT)
    # = 10.4506 - 100 + 100*exp(-0.05) ≈ 5.5735
    price = black_scholes_price(
        S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.20, option_type="put"
    )
    assert price == pytest.approx(5.5735, rel=0.001)


def test_bs_itm_call_intrinsic_floor():
    # Deep ITM call near expiry collapses to intrinsic
    price = black_scholes_price(
        S=150.0, K=100.0, T=0.001, r=0.05, sigma=0.20, option_type="call"
    )
    assert price == pytest.approx(50.0, abs=0.1)


def test_bs_otm_put_zero_at_expiry():
    # OTM put at T=0 → intrinsic = max(K-S, 0) = 0
    price = black_scholes_price(
        S=110.0, K=100.0, T=0.0, r=0.05, sigma=0.20, option_type="put"
    )
    assert price == pytest.approx(0.0, abs=1e-6)


def test_bs_itm_put_at_expiry_returns_intrinsic():
    # ITM put at T=0 → intrinsic = K - S = 10
    price = black_scholes_price(
        S=90.0, K=100.0, T=0.0, r=0.05, sigma=0.20, option_type="put"
    )
    assert price == pytest.approx(10.0, abs=1e-6)


def test_bs_accepts_short_form_option_type():
    # Helper accepts "C"/"P" as well as "call"/"put"
    p1 = black_scholes_price(
        S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.20, option_type="C"
    )
    p2 = black_scholes_price(
        S=100.0, K=100.0, T=1.0, r=0.05, sigma=0.20, option_type="call"
    )
    assert p1 == pytest.approx(p2)


def test_bs_negative_T_treated_as_expired():
    # Sim-time past expiration → treat T=0, return intrinsic
    price = black_scholes_price(
        S=110.0, K=100.0, T=-0.01, r=0.05, sigma=0.20, option_type="call"
    )
    assert price == pytest.approx(10.0, abs=1e-6)


def test_bs_zero_sigma_returns_discounted_intrinsic():
    # σ=0 → no time value; for an ITM call, BS reduces to S - K*exp(-rT)
    price = black_scholes_price(
        S=110.0, K=100.0, T=1.0, r=0.05, sigma=0.0, option_type="call"
    )
    expected = 110.0 - 100.0 * math.exp(-0.05)
    assert price == pytest.approx(expected, abs=1e-6)


def test_helper_initial_state_is_empty():
    h = OptionsMTMHelper()
    assert h._iv_by_symbol == {}
    assert h._iv_by_expiry == {}
    assert h._iv_by_underlying == {}
    assert h._mid_by_symbol == {}


def test_helper_observe_populates_all_four_caches():
    h = OptionsMTMHelper()
    sim = datetime(2024, 6, 1, tzinfo=timezone.utc)
    h.observe(
        symbol="O:SPY240621C00500000",
        mid=12.5,
        iv=0.22,
        sim_time=sim,
        underlying="SPY",
        expiration_str="2024-06-21",
    )
    assert h._iv_by_symbol["O:SPY240621C00500000"].iv == pytest.approx(0.22)
    assert h._iv_by_symbol["O:SPY240621C00500000"].sim_time == sim
    assert h._iv_by_expiry[("SPY", "2024-06-21")].iv == pytest.approx(0.22)
    assert h._iv_by_underlying["SPY"].iv == pytest.approx(0.22)
    assert h._mid_by_symbol["O:SPY240621C00500000"].mid == pytest.approx(12.5)
    assert h._mid_by_symbol["O:SPY240621C00500000"].sim_time == sim


def test_helper_observe_overwrites_with_newer_sim_time():
    h = OptionsMTMHelper()
    older = datetime(2024, 6, 1, tzinfo=timezone.utc)
    newer = datetime(2024, 6, 2, tzinfo=timezone.utc)
    h.observe("O:SPY240621C00500000", 10.0, 0.20, older, "SPY", "2024-06-21")
    h.observe("O:SPY240621C00500000", 11.0, 0.21, newer, "SPY", "2024-06-21")
    assert h._iv_by_symbol["O:SPY240621C00500000"].iv == pytest.approx(0.21)
    assert h._mid_by_symbol["O:SPY240621C00500000"].mid == pytest.approx(11.0)
    # And the (underlying, expiry) cache should also have the newer entry
    assert h._iv_by_expiry[("SPY", "2024-06-21")].iv == pytest.approx(0.21)


def test_helper_observe_ignores_non_positive_iv():
    # Some chain rows have iv=0 (data quality). Don't poison the caches.
    h = OptionsMTMHelper()
    sim = datetime(2024, 6, 1, tzinfo=timezone.utc)
    h.observe("O:SPY240621C00500000", 10.0, 0.0, sim, "SPY", "2024-06-21")
    assert "O:SPY240621C00500000" not in h._iv_by_symbol
    assert ("SPY", "2024-06-21") not in h._iv_by_expiry
    assert "SPY" not in h._iv_by_underlying
    # Mid should still be cached (mid > 0 is independent signal)
    assert h._mid_by_symbol["O:SPY240621C00500000"].mid == pytest.approx(10.0)


def test_helper_observe_ignores_non_positive_mid():
    h = OptionsMTMHelper()
    sim = datetime(2024, 6, 1, tzinfo=timezone.utc)
    h.observe("O:SPY240621C00500000", 0.0, 0.20, sim, "SPY", "2024-06-21")
    assert "O:SPY240621C00500000" not in h._mid_by_symbol
    # IV is positive, so its caches DO get populated
    assert h._iv_by_symbol["O:SPY240621C00500000"].iv == pytest.approx(0.20)
