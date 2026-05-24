import os
import tempfile
import pandas as pd
import pytest
from datetime import date
from coordinator.services.data_service import DataService


@pytest.fixture
def svc(tmp_path):
    market = tmp_path / "market"
    custom = tmp_path / "custom"
    market.mkdir()
    custom.mkdir()
    return DataService(market_data_dir=str(market), custom_data_dir=str(custom))


def _write_contract_bars(svc, provider, symbol, df):
    svc.save_market_data(provider, symbol, "1day", df)


def test_list_option_contracts_finds_matching_contracts(svc):
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2024-10-25"]),
        "open": [5.0], "high": [6.0], "low": [4.0], "close": [5.5], "volume": [100],
    })
    _write_contract_bars(svc, "polygon", "SPY241029C00580000", df)
    _write_contract_bars(svc, "polygon", "SPY241029P00580000", df)
    _write_contract_bars(svc, "polygon", "SPY241115C00580000", df)  # different expiration
    _write_contract_bars(svc, "polygon", "QQQ241029C00400000", df)  # different underlying

    contracts = svc.list_option_contracts("polygon", "SPY", date(2024, 10, 29))
    assert sorted(contracts) == ["SPY241029C00580000", "SPY241029P00580000"]


def test_list_option_contracts_empty(svc):
    assert svc.list_option_contracts("polygon", "SPY", date(2024, 10, 29)) == []


def test_list_option_expirations(svc):
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2024-10-25"]),
        "open": [5.0], "high": [6.0], "low": [4.0], "close": [5.5], "volume": [100],
    })
    _write_contract_bars(svc, "polygon", "SPY241029C00580000", df)
    _write_contract_bars(svc, "polygon", "SPY241115C00580000", df)
    _write_contract_bars(svc, "polygon", "SPY241115P00580000", df)

    exps = svc.list_option_expirations("polygon", "SPY")
    assert exps == [date(2024, 10, 29), date(2024, 11, 15)]


def test_build_chain_loads_and_builds(svc):
    df1 = pd.DataFrame({
        "timestamp": pd.to_datetime(["2024-10-25", "2024-10-28"]),
        "open": [5.0, 5.5], "high": [6.0, 6.5], "low": [4.0, 4.5],
        "close": [5.5, 6.0], "volume": [100, 150],
    })
    df2 = pd.DataFrame({
        "timestamp": pd.to_datetime(["2024-10-25", "2024-10-28"]),
        "open": [3.0, 3.5], "high": [4.0, 4.5], "low": [2.0, 2.5],
        "close": [3.5, 4.0], "volume": [80, 120],
    })
    _write_contract_bars(svc, "polygon", "SPY241029C00580000", df1)
    _write_contract_bars(svc, "polygon", "SPY241029P00580000", df2)

    chain = svc.build_chain("polygon", "SPY", date(2024, 10, 29), as_of=pd.Timestamp("2024-10-28"))
    assert len(chain) == 2
    call = chain[chain["option_type"] == "call"].iloc[0]
    assert call["last"] == pytest.approx(6.0)
    assert call["strike"] == 580.0
