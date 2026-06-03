import json
import pytest
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from coordinator.database.models import Base, OptimizationSession, BacktestRun
from coordinator.services.validation.optimization_session import create_session
from coordinator.services.validation.regime import tag_regimes, regime_conditional_metrics
from coordinator.services.validation.bootstrap import bootstrap_metrics
from coordinator.services.validation.walk_forward import concatenate_oos_curves
from coordinator.services.validation.report import ReportInputs, build_html_report


@pytest.fixture
def db():
    from coordinator.database.models import Algorithm

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as s:
        algo = Algorithm(id="test-algo", name="test-algo", repo_url="https://example.com/algo")
        s.add(algo)
        s.flush()
        yield s


def test_lab_pipeline_end_to_end(db, tmp_path):
    # 1) Pre-register session
    sess = create_session(
        db,
        name="e2e-smoke-001",
        hypothesis="Smoke: dummy walk-forward produces a renderable report",
        algorithm_id="test-algo",
        base_config={},
        parameter_space={"x": [1, 2]},
        pre_registered_criteria={"oos_sharpe_lci": -10.0, "max_dd_uci": 1.0},
        date_range_start=date(2023, 1, 1),
        date_range_end=date(2024, 12, 31),
    )
    db.commit()

    # 2) Skip real backtest — synthesize two OOS equity curves on disk.
    # Prepend 1.0 before cumprod so the first value is exactly the anchor (1000.0).
    rng = np.random.default_rng(42)
    f1 = tmp_path / "fold1_equity.parquet"
    pd.DataFrame({
        "timestamp": pd.date_range("2024-01-01", periods=180, freq="D"),
        "equity": 1000.0 * np.concatenate([[1.0], np.cumprod(1.0 + rng.normal(0.001, 0.02, 179))]),
    }).to_parquet(f1)
    f2 = tmp_path / "fold2_equity.parquet"
    pd.DataFrame({
        "timestamp": pd.date_range("2024-07-01", periods=180, freq="D"),
        "equity": 1000.0 * np.concatenate([[1.0], np.cumprod(1.0 + rng.normal(0.0005, 0.025, 179))]),
    }).to_parquet(f2)

    # 3) Concatenate
    equity = concatenate_oos_curves([f1, f2])
    assert len(equity) == 360
    assert equity.iloc[0] == 1000.0

    # 4) Regime tag + conditional metrics
    regimes = tag_regimes(equity, lookback_days=60)
    regime_m = regime_conditional_metrics(equity, regimes)
    assert isinstance(regime_m, dict)

    # 5) Bootstrap CIs
    boot = bootstrap_metrics(equity, n_resamples=200, seed=0)
    assert "sharpe" in boot
    assert "max_drawdown" in boot

    # 6) Build report
    inputs = ReportInputs(
        session=sess,
        oos_equity_curve=equity,
        regimes=regimes,
        bootstrap_metrics={k: v.__dict__ for k, v in boot.items()},
        regime_metrics=regime_m,
        corrected_p_values=[],
    )
    out = build_html_report(inputs, out_dir=tmp_path / "report")
    assert out["html"].exists()
    html = out["html"].read_text()
    assert "e2e-smoke-001" in html
    assert "<table" in html
    assert "Bootstrap CIs" in html
