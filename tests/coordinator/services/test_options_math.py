import pytest
import math
from coordinator.services.options_math import bs_price, bs_iv, bs_greeks, estimate_spread


# ── Black-Scholes price tests ────────────────────────────────────────────────
# Reference: Hull, "Options, Futures, and Other Derivatives"
# S=100, K=100, T=0.25, r=0.05, sigma=0.20 → call ≈ 4.615, put ≈ 3.373
# (computed via N(d1=0.175)=0.5695, N(d2=0.075)=0.5299; Hull's 3.88/2.64 use different params)

class TestBSPrice:
    def test_atm_call(self):
        price = bs_price(S=100, K=100, T=0.25, r=0.05, sigma=0.20, option_type="call")
        assert price == pytest.approx(4.615, abs=0.02)

    def test_atm_put(self):
        price = bs_price(S=100, K=100, T=0.25, r=0.05, sigma=0.20, option_type="put")
        assert price == pytest.approx(3.373, abs=0.02)

    def test_put_call_parity(self):
        """C - P = S - K*exp(-rT)"""
        call = bs_price(S=100, K=100, T=0.25, r=0.05, sigma=0.20, option_type="call")
        put = bs_price(S=100, K=100, T=0.25, r=0.05, sigma=0.20, option_type="put")
        parity = 100 - 100 * math.exp(-0.05 * 0.25)
        assert (call - put) == pytest.approx(parity, abs=0.001)

    def test_deep_itm_call(self):
        price = bs_price(S=150, K=100, T=0.25, r=0.05, sigma=0.20, option_type="call")
        intrinsic = 150 - 100
        assert price >= intrinsic
        assert price == pytest.approx(51.24, abs=0.10)

    def test_deep_otm_call(self):
        price = bs_price(S=50, K=100, T=0.25, r=0.05, sigma=0.20, option_type="call")
        assert price < 0.01

    def test_at_expiry_itm(self):
        price = bs_price(S=105, K=100, T=1e-10, r=0.05, sigma=0.20, option_type="call")
        assert price == pytest.approx(5.0, abs=0.01)

    def test_at_expiry_otm(self):
        price = bs_price(S=95, K=100, T=1e-10, r=0.05, sigma=0.20, option_type="call")
        assert price == pytest.approx(0.0, abs=0.01)

    def test_higher_vol_means_higher_price(self):
        low_vol = bs_price(S=100, K=100, T=0.25, r=0.05, sigma=0.10, option_type="call")
        high_vol = bs_price(S=100, K=100, T=0.25, r=0.05, sigma=0.40, option_type="call")
        assert high_vol > low_vol

    def test_more_time_means_higher_price(self):
        short = bs_price(S=100, K=100, T=0.1, r=0.05, sigma=0.20, option_type="call")
        long = bs_price(S=100, K=100, T=1.0, r=0.05, sigma=0.20, option_type="call")
        assert long > short


class TestBSIV:
    def test_round_trip_atm(self):
        price = bs_price(S=100, K=100, T=0.25, r=0.05, sigma=0.20, option_type="call")
        recovered = bs_iv(price=price, S=100, K=100, T=0.25, r=0.05, option_type="call")
        assert recovered == pytest.approx(0.20, abs=0.001)

    def test_round_trip_itm_put(self):
        price = bs_price(S=90, K=100, T=0.5, r=0.05, sigma=0.30, option_type="put")
        recovered = bs_iv(price=price, S=90, K=100, T=0.5, r=0.05, option_type="put")
        assert recovered == pytest.approx(0.30, abs=0.001)

    def test_round_trip_high_vol(self):
        price = bs_price(S=100, K=100, T=0.25, r=0.05, sigma=0.80, option_type="call")
        recovered = bs_iv(price=price, S=100, K=100, T=0.25, r=0.05, option_type="call")
        assert recovered == pytest.approx(0.80, abs=0.005)

    def test_returns_none_for_impossible_price(self):
        # Deep ITM put: intrinsic = K - S = 50; price=0.001 is below intrinsic floor
        result = bs_iv(price=0.001, S=50, K=100, T=0.25, r=0.05, option_type="put")
        assert result is None

    def test_returns_none_for_zero_price(self):
        result = bs_iv(price=0.0, S=100, K=100, T=0.25, r=0.05, option_type="call")
        assert result is None

    def test_returns_none_at_expiry(self):
        result = bs_iv(price=5.0, S=105, K=100, T=0.0, r=0.05, option_type="call")
        assert result is None


class TestBSGreeks:
    def test_atm_call_delta_near_half(self):
        g = bs_greeks(S=100, K=100, T=0.25, r=0.05, sigma=0.20, option_type="call")
        assert g["delta"] == pytest.approx(0.57, abs=0.03)

    def test_atm_put_delta_negative(self):
        g = bs_greeks(S=100, K=100, T=0.25, r=0.05, sigma=0.20, option_type="put")
        assert g["delta"] == pytest.approx(-0.43, abs=0.03)

    def test_theta_is_negative(self):
        g = bs_greeks(S=100, K=100, T=0.25, r=0.05, sigma=0.20, option_type="call")
        assert g["theta"] < 0

    def test_vega_is_positive(self):
        g = bs_greeks(S=100, K=100, T=0.25, r=0.05, sigma=0.20, option_type="call")
        assert g["vega"] > 0

    def test_gamma_is_positive(self):
        g = bs_greeks(S=100, K=100, T=0.25, r=0.05, sigma=0.20, option_type="call")
        assert g["gamma"] > 0


class TestEstimateSpread:
    def test_high_volume_tight_spread(self):
        spread = estimate_spread(price=5.0, volume=10000)
        assert spread < 0.10

    def test_low_volume_wide_spread(self):
        spread = estimate_spread(price=5.0, volume=1)
        assert spread > 0.20

    def test_zero_volume_widest(self):
        zero_spread = estimate_spread(price=5.0, volume=0)
        some_spread = estimate_spread(price=5.0, volume=100)
        assert zero_spread > some_spread

    def test_spread_scales_with_price(self):
        cheap = estimate_spread(price=1.0, volume=100)
        expensive = estimate_spread(price=50.0, volume=100)
        assert expensive > cheap

    def test_spread_never_negative(self):
        assert estimate_spread(price=0.01, volume=0) >= 0
        assert estimate_spread(price=0.0, volume=0) >= 0

    def test_spread_never_exceeds_twice_price(self):
        spread = estimate_spread(price=1.0, volume=0)
        assert spread <= 2.0
