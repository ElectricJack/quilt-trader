import json
import pytest
from datetime import datetime, timezone

from coordinator.database.models import OptimizationSession


def test_optimization_session_basic_fields():
    sess = OptimizationSession(
        name="tsmom-2026-05-27",
        hypothesis="Daily ensemble TSMOM on BTC/ETH produces OOS Sharpe lower-CI > 0.5",
        parameter_space=json.dumps({"vol_target": [0.10, 0.15, 0.20]}),
        pre_registered_criteria=json.dumps({"oos_sharpe_lci": 0.5, "max_dd_uci": 0.35}),
        status="open",
    )
    assert sess.name == "tsmom-2026-05-27"
    assert "OOS Sharpe" in sess.hypothesis
    assert sess.status == "open"
