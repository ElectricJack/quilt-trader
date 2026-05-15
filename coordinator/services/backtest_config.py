"""Pydantic config models for backtest runs.

TradingFee mirrors Lumibot's API (flat + percent + maker/taker).
SlippageModel is our own (Lumibot doesn't simulate slippage in the
backtest broker). Default market_bps=5.0 — conservative by design.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class TradingFee(BaseModel):
    flat_fee: float = Field(default=0.0, ge=0)
    percent_fee: float = Field(default=0.0, ge=0)   # decimal: 0.001 = 0.1%
    maker: bool = True   # applies to limit / stop_limit
    taker: bool = True   # applies to market / stop


class SlippageModel(BaseModel):
    market_bps: float = Field(default=5.0, ge=0)
    limit_bps: float = Field(default=0.0, ge=0)
    use_bar_range: bool = False
    volume_impact_bps_per_pct: float = Field(default=0.0, ge=0)
