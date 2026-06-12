"""Failing tests for SDK audit finding F19.

See docs/superpowers/research/2026-06-11-correctness-audit-findings.md §6.
The test asserts the CORRECT behavior; it fails today because the bug exists
and must pass once the finding is fixed.
"""
import pytest

from sdk.signals import SignalLeg, SignalType


@pytest.mark.parametrize(
    "quantity",
    [0, -5, float("nan"), float("inf")],
    ids=["zero", "negative", "nan", "inf"],
)
def test_f19_signal_leg_rejects_nonsense_quantity(quantity):
    """F19: sdk/signals.py:53-54 — SignalLeg.__post_init__ validates only
    asset_type; quantity 0, negative, NaN, and inf all pass straight through
    this user-algo -> coordinator system boundary."""
    with pytest.raises(ValueError):
        SignalLeg(symbol="AAPL", signal_type=SignalType.BUY, quantity=quantity)
