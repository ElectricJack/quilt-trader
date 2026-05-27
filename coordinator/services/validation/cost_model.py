from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from coordinator.services.backtest_config import SlippageModel, TradingFee


class CostBundle(BaseModel):
    fees: list[TradingFee] = Field(default_factory=lambda: [TradingFee()])
    slippage: SlippageModel = Field(default_factory=SlippageModel)


class CostModelProfile(BaseModel):
    name: str = "default"
    bundles: dict[str, CostBundle] = Field(default_factory=dict)
    fallback: CostBundle = Field(default_factory=CostBundle)

    @classmethod
    def default(cls) -> "CostModelProfile":
        return cls(name="default", bundles={}, fallback=CostBundle())

    def resolve(self, *, venue: str, asset_type: str, symbol: str) -> CostBundle:
        keys = (
            f"{venue}:{asset_type}:{symbol}",
            f"{venue}:{asset_type}",
            f"{venue}",
            f"{asset_type}",
        )
        for key in keys:
            if key in self.bundles:
                return self.bundles[key]
        return self.fallback

    @classmethod
    def from_yaml(cls, path: str | Path) -> "CostModelProfile":
        data = yaml.safe_load(Path(path).read_text())
        return cls.model_validate(data)
