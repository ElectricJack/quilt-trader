"""Tests for the conservative options MTM helper."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from coordinator.services.options_mtm import (
    FALLBACK_SIGMA,
    RISK_FREE_RATE,
    _IVCacheEntry,
    _MidCacheEntry,
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
