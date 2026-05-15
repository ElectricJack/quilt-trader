"""Tests for backtest tearsheet generation (Spec D T1)."""
import pytest
import pandas as pd
from pathlib import Path
from coordinator.services.backtest_tearsheet import generate_tearsheet


def test_generate_tearsheet_creates_html(tmp_path):
    # Synthetic daily returns over 60 days
    idx = pd.date_range("2024-01-01", periods=60, freq="D", tz="UTC")
    strategy_pv = pd.Series([100_000 + i * 100 for i in range(60)], index=idx, name="strategy")
    bench_pv = pd.Series([100_000 + i * 80 for i in range(60)], index=idx, name="benchmark")

    out_path = tmp_path / "tearsheet.html"
    generate_tearsheet(strategy_pv, bench_pv, title="Test", output=str(out_path), risk_free_rate=0.04)
    assert out_path.exists()
    content = out_path.read_text()
    assert "<html" in content.lower() or "<!doctype" in content.lower()


def test_generate_tearsheet_handles_missing_benchmark(tmp_path):
    idx = pd.date_range("2024-01-01", periods=10, freq="D", tz="UTC")
    strategy_pv = pd.Series([100_000 + i * 50 for i in range(10)], index=idx, name="strategy")
    out_path = tmp_path / "tearsheet.html"
    generate_tearsheet(strategy_pv, None, title="Test", output=str(out_path))
    # quantstats can produce a no-benchmark report
    assert out_path.exists() or out_path.with_suffix(".html").exists()
