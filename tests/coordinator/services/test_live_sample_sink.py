# tests/coordinator/services/test_live_sample_sink.py
from pathlib import Path
import asyncio
import pytest
import pyarrow.parquet as pq


@pytest.mark.asyncio
async def test_sink_buffers_and_flushes_per_run_parquet(tmp_path: Path):
    from coordinator.services.live_sample_sink import LiveSampleSink
    sink = LiveSampleSink(base_dir=tmp_path, buffer_size=3, flush_interval_seconds=60)
    for i in range(3):
        await sink.add_equity_sample("d1", "r1", {
            "timestamp": f"2026-05-16T12:00:0{i}Z",
            "portfolio_value": 100.0 + i,
            "cash": 50.0 + i,
        })
    out = tmp_path / "d1" / "r1" / "equity.parquet"
    assert out.exists()
    df = pq.read_table(out).to_pandas()
    assert list(df["portfolio_value"]) == [100.0, 101.0, 102.0]


@pytest.mark.asyncio
async def test_force_flush_writes_pending_rows(tmp_path: Path):
    from coordinator.services.live_sample_sink import LiveSampleSink
    sink = LiveSampleSink(base_dir=tmp_path, buffer_size=100, flush_interval_seconds=60)
    await sink.add_trade_sample("d1", "r1", {
        "timestamp": "2026-05-16T12:00:00Z", "symbol": "AAPL",
        "asset_type": "equities", "side": "buy", "quantity": 10.0,
        "requested_price": 100.0, "fill_price": 100.5,
        "slippage_dollars": 5.0, "slippage_bps_applied": 0.5,
        "fees": 1.0, "fee_breakdown": "{}", "signal_id": "s1",
        "realized_pnl": None,
    })
    await sink.flush()
    out = tmp_path / "d1" / "r1" / "trades.parquet"
    assert out.exists()
    df = pq.read_table(out).to_pandas()
    assert df.iloc[0]["symbol"] == "AAPL"
    assert df.iloc[0]["quantity"] == 10.0


@pytest.mark.asyncio
async def test_sink_appends_across_multiple_flushes(tmp_path: Path):
    from coordinator.services.live_sample_sink import LiveSampleSink
    sink = LiveSampleSink(base_dir=tmp_path, buffer_size=2, flush_interval_seconds=60)
    for i in range(2):
        await sink.add_equity_sample("d1", "r1", {
            "timestamp": f"2026-05-16T12:00:0{i}Z",
            "portfolio_value": 100.0 + i, "cash": 50.0,
        })
    for i in range(2):
        await sink.add_equity_sample("d1", "r1", {
            "timestamp": f"2026-05-16T12:01:0{i}Z",
            "portfolio_value": 200.0 + i, "cash": 50.0,
        })
    out = tmp_path / "d1" / "r1" / "equity.parquet"
    df = pq.read_table(out).to_pandas()
    assert len(df) == 4
    assert list(df["portfolio_value"]) == [100.0, 101.0, 200.0, 201.0]
