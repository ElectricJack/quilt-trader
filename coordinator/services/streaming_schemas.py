# coordinator/services/streaming_schemas.py
"""Shared parquet schemas for backtest and live streaming pipelines.

Both pipelines emit identically-shaped equity samples and trade records, so
they share these schemas verbatim.  Keep field order and types stable —
parquet files written by one pipeline must be readable by the other's
consumers (notably backtest_finalizer).
"""
import pyarrow as pa

EQUITY_SCHEMA = pa.schema([
    ("timestamp", pa.timestamp("ns")),
    ("portfolio_value", pa.float64()),
    ("cash", pa.float64()),
])

TRADE_SCHEMA = pa.schema([
    ("timestamp", pa.timestamp("ns")),
    ("symbol", pa.string()),
    ("asset_type", pa.string()),
    ("side", pa.string()),
    ("quantity", pa.float64()),
    ("requested_price", pa.float64()),
    ("fill_price", pa.float64()),
    ("slippage_dollars", pa.float64()),
    ("slippage_bps_applied", pa.float64()),
    ("fees", pa.float64()),
    ("fee_breakdown", pa.string()),
    ("signal_id", pa.string()),
    ("realized_pnl", pa.float64()),
])
