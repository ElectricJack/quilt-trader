import pytest
from datetime import datetime, timezone
from sqlalchemy import select

from coordinator.database.models import (
    Account, Algorithm, Worker, AlgorithmInstance, AlgorithmRun,
    TradeLog, Position, AccountCashFlow, AccountSnapshot,
)


@pytest.mark.asyncio
async def test_create_account(db_session):
    account = Account(
        name="Alpaca Main", broker_type="alpaca", credentials="encrypted-blob",
        supported_asset_types=["equities", "options", "crypto"],
        options_level=3, account_features=["margin", "short_selling"], pdt_mode="warn",
    )
    db_session.add(account)
    await db_session.flush()
    result = await db_session.execute(select(Account).where(Account.name == "Alpaca Main"))
    fetched = result.scalar_one()
    assert fetched.id is not None
    assert fetched.broker_type == "alpaca"
    assert fetched.supported_asset_types == ["equities", "options", "crypto"]
    assert fetched.pdt_mode == "warn"
    assert fetched.locked_by is None
    assert fetched.created_at is not None


@pytest.mark.asyncio
async def test_create_algorithm(db_session):
    algo = Algorithm(
        repo_url="https://github.com/ElectricJack/momentum-scalper",
        name="momentum-scalper", description="Intraday momentum",
        version="1.0.0", commit_hash="abc123",
        required_asset_types=["equities", "options"], required_options_level=3,
        required_account_features=["margin"], supported_brokers=None,
        data_dependencies=[{"name": "alpha-picks-scraper", "repo": "ElectricJack/alpha-picks-scraper"}],
        config_schema={"parameters": [{"name": "risk_per_trade", "type": "float"}]},
        custom_events=[{"name": "unusual_volume", "severity": "info"}],
        install_status="installed",
    )
    db_session.add(algo)
    await db_session.flush()
    result = await db_session.execute(select(Algorithm).where(Algorithm.name == "momentum-scalper"))
    fetched = result.scalar_one()
    assert fetched.id is not None
    assert fetched.data_dependencies[0]["name"] == "alpha-picks-scraper"


@pytest.mark.asyncio
async def test_create_worker(db_session):
    worker = Worker(name="Pi Living Room", tailscale_ip="100.64.0.1", status="online", max_algorithms=3)
    db_session.add(worker)
    await db_session.flush()
    result = await db_session.execute(select(Worker).where(Worker.name == "Pi Living Room"))
    fetched = result.scalar_one()
    assert fetched.id is not None
    assert fetched.tailscale_ip == "100.64.0.1"


@pytest.mark.asyncio
async def test_account_default_timestamps(db_session):
    account = Account(name="Test", broker_type="tradier", credentials="enc",
                      supported_asset_types=["equities"], pdt_mode="off")
    db_session.add(account)
    await db_session.flush()
    assert account.created_at is not None
    assert account.updated_at is not None


@pytest.mark.asyncio
async def test_algorithm_instance_relationships(db_session):
    account = Account(name="Test Account", broker_type="alpaca", credentials="enc",
                      supported_asset_types=["equities"], pdt_mode="off")
    algo = Algorithm(repo_url="https://github.com/test/algo", name="test-algo", install_status="installed")
    worker = Worker(name="Test Worker", tailscale_ip="100.64.0.2", status="online")
    db_session.add_all([account, algo, worker])
    await db_session.flush()
    instance = AlgorithmInstance(algorithm_id=algo.id, account_id=account.id, worker_id=worker.id, status="stopped")
    db_session.add(instance)
    await db_session.flush()
    result = await db_session.execute(select(AlgorithmInstance).where(AlgorithmInstance.id == instance.id))
    fetched = result.scalar_one()
    assert fetched.algorithm_id == algo.id
    assert fetched.account_id == account.id


@pytest.mark.asyncio
async def test_algorithm_run(db_session):
    account = Account(name="Run Test", broker_type="alpaca", credentials="enc",
                      supported_asset_types=["equities"], pdt_mode="off")
    algo = Algorithm(repo_url="https://github.com/test/algo", name="run-test", install_status="installed")
    worker = Worker(name="Run Worker", tailscale_ip="100.64.0.3", status="online")
    db_session.add_all([account, algo, worker])
    await db_session.flush()
    instance = AlgorithmInstance(algorithm_id=algo.id, account_id=account.id, worker_id=worker.id, status="running")
    db_session.add(instance)
    await db_session.flush()
    run = AlgorithmRun(instance_id=instance.id, run_number=1, status="running", starting_equity=50000.0)
    db_session.add(run)
    await db_session.flush()
    result = await db_session.execute(select(AlgorithmRun).where(AlgorithmRun.instance_id == instance.id))
    fetched = result.scalar_one()
    assert fetched.run_number == 1
    assert fetched.starting_equity == 50000.0


@pytest.mark.asyncio
async def test_trade_log(db_session):
    account = Account(name="Trade Account", broker_type="alpaca", credentials="enc",
                      supported_asset_types=["equities"], pdt_mode="off")
    db_session.add(account)
    await db_session.flush()
    trade = TradeLog(account_id=account.id, source="manual", symbol="AAPL", asset_type="equities",
                     side="buy", quantity=100.0, order_type="market", filled_price=150.50,
                     fees=1.00, fee_breakdown={"commission": 0.50, "exchange_fee": 0.50})
    db_session.add(trade)
    await db_session.flush()
    result = await db_session.execute(select(TradeLog).where(TradeLog.symbol == "AAPL"))
    fetched = result.scalar_one()
    assert fetched.filled_price == 150.50
    assert fetched.group_id is not None


@pytest.mark.asyncio
async def test_position(db_session):
    account = Account(name="Pos Account", broker_type="alpaca", credentials="enc",
                      supported_asset_types=["equities", "options"], pdt_mode="off")
    db_session.add(account)
    await db_session.flush()
    position = Position(account_id=account.id, strategy_type="bull_call_spread",
                        legs=[{"symbol": "AAPL250620C00200000", "side": "buy", "quantity": 1}],
                        status="open", net_cost=2.50)
    db_session.add(position)
    await db_session.flush()
    result = await db_session.execute(select(Position).where(Position.id == position.id))
    fetched = result.scalar_one()
    assert fetched.strategy_type == "bull_call_spread"
    assert len(fetched.legs) == 1


@pytest.mark.asyncio
async def test_cash_flow_and_snapshot(db_session):
    account = Account(name="CF Account", broker_type="tradier", credentials="enc",
                      supported_asset_types=["equities"], pdt_mode="off")
    db_session.add(account)
    await db_session.flush()
    cf = AccountCashFlow(account_id=account.id, type="deposit", amount=10000.0, notes="Initial")
    snap = AccountSnapshot(account_id=account.id, total_value=10000.0, cash=10000.0,
                           positions_value=0.0, net_deposits_cumulative=10000.0, source="cash_flow")
    db_session.add_all([cf, snap])
    await db_session.flush()
    cf_result = await db_session.execute(select(AccountCashFlow).where(AccountCashFlow.account_id == account.id))
    assert cf_result.scalar_one().amount == 10000.0
    snap_result = await db_session.execute(select(AccountSnapshot).where(AccountSnapshot.account_id == account.id))
    assert snap_result.scalar_one().total_value == 10000.0
