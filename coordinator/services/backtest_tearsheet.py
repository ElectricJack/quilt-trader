"""quantstats HTML tearsheet generation for completed backtest runs.

Mirrors Lumibot's create_tearsheet pattern (lumibot/tools/indicators.py:944)
but stripped to the essentials: strategy returns + optional benchmark returns
→ HTML file. Spec D §6.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


def generate_tearsheet(
    strategy_pv: pd.Series,
    benchmark_pv: Optional[pd.Series],
    *,
    title: str,
    output: str,
    risk_free_rate: float = 0.04,
) -> Optional[str]:
    """Generate an HTML tearsheet at `output`. Returns the path on success, None on failure.

    `strategy_pv` and `benchmark_pv` are portfolio-value series (NOT returns).
    """
    try:
        import quantstats as qs
        import contextlib
        import warnings

        strategy_returns = strategy_pv.pct_change().fillna(0)
        strategy_returns.name = "strategy"
        # quantstats internally compares tz-aware and tz-naive dtypes; strip tz to avoid errors
        if hasattr(strategy_returns.index, "tz") and strategy_returns.index.tz is not None:
            strategy_returns.index = strategy_returns.index.tz_localize(None)
        bench_returns = None
        if benchmark_pv is not None and not benchmark_pv.empty:
            bench_returns = benchmark_pv.pct_change().fillna(0)
            bench_returns.name = benchmark_pv.name or "benchmark"
            if hasattr(bench_returns.index, "tz") and bench_returns.index.tz is not None:
                bench_returns.index = bench_returns.index.tz_localize(None)

        os.makedirs(os.path.dirname(output) or ".", exist_ok=True)

        with open(os.devnull, "w") as devnull, \
             contextlib.redirect_stdout(devnull), \
             contextlib.redirect_stderr(devnull), \
             warnings.catch_warnings():
            warnings.simplefilter("ignore")
            qs.reports.html(
                strategy_returns,
                benchmark=bench_returns,
                title=title,
                output=output,
                rf=risk_free_rate,
            )
        return output
    except Exception as exc:
        logger.warning("Tearsheet generation failed for %s: %s", title, exc)
        return None
