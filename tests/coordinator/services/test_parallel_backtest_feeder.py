import pytest
from datetime import datetime, timezone
from coordinator.services.parallel_backtest_feeder import ParallelBacktestFeeder


@pytest.mark.asyncio
async def test_feeder_writes_decision_log_per_signal(test_app, db_session):
    from coordinator.database.models import Algorithm, AlgorithmInstance, Account, Worker, DecisionLog
    algo = Algorithm(name="x", repo_url="https://e/x", install_status="installed")
    account = Account(name="a", broker_type="alpaca", environment="paper",
                      credentials="{}", supported_asset_types=["equities"], pdt_mode="off")
    worker = Worker(name="w")
    db_session.add_all([algo, account, worker]); await db_session.flush()
    inst = AlgorithmInstance(algorithm_id=algo.id, account_id=account.id, worker_id=worker.id, status="running")
    db_session.add(inst); await db_session.commit()

    from coordinator.api.dependencies import get_container
    feeder = ParallelBacktestFeeder(instance_id=inst.id, session_factory=get_container().session_factory)

    from sdk.signals import Signal, SignalLeg, SignalType
    sig = Signal(legs=[SignalLeg(symbol="SPY", signal_type=SignalType.BUY, quantity=1,
                                  asset_type="equities")])
    await feeder.on_signals_emitted_async(datetime(2024, 1, 15, tzinfo=timezone.utc), [sig])

    from sqlalchemy import select
    rows = (await db_session.execute(
        select(DecisionLog).where(DecisionLog.instance_id == inst.id)
                          .where(DecisionLog.mode == "backtest")
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].signals_produced[0]["legs"][0]["symbol"] == "SPY"


@pytest.mark.asyncio
async def test_feeder_writes_correct_signal_type(test_app, db_session):
    """Verify signal_type enum value is serialized correctly."""
    from coordinator.database.models import Algorithm, AlgorithmInstance, Account, Worker, DecisionLog
    algo = Algorithm(name="y", repo_url="https://e/y", install_status="installed")
    account = Account(name="b", broker_type="alpaca", environment="paper",
                      credentials="{}", supported_asset_types=["equities"], pdt_mode="off")
    worker = Worker(name="w2")
    db_session.add_all([algo, account, worker]); await db_session.flush()
    inst = AlgorithmInstance(algorithm_id=algo.id, account_id=account.id, worker_id=worker.id, status="running")
    db_session.add(inst); await db_session.commit()

    from coordinator.api.dependencies import get_container
    feeder = ParallelBacktestFeeder(instance_id=inst.id, session_factory=get_container().session_factory)

    from sdk.signals import Signal, SignalLeg, SignalType
    sig = Signal(legs=[SignalLeg(symbol="AAPL", signal_type=SignalType.SELL, quantity=5,
                                  asset_type="equities")])
    await feeder.on_signals_emitted_async(datetime(2024, 2, 1, tzinfo=timezone.utc), [sig])

    from sqlalchemy import select
    rows = (await db_session.execute(
        select(DecisionLog).where(DecisionLog.instance_id == inst.id)
                          .where(DecisionLog.mode == "backtest")
    )).scalars().all()
    assert len(rows) == 1
    leg = rows[0].signals_produced[0]["legs"][0]
    assert leg["symbol"] == "AAPL"
    assert leg["signal_type"] == "sell"
    assert leg["quantity"] == 5
    assert leg["asset_type"] == "equities"
    assert rows[0].signals_produced[0]["strategy_type"] == "single"


@pytest.mark.asyncio
async def test_feeder_multiple_signals_writes_multiple_rows(test_app, db_session):
    """Each signal in the list becomes a separate DecisionLog row."""
    from coordinator.database.models import Algorithm, AlgorithmInstance, Account, Worker, DecisionLog
    algo = Algorithm(name="z", repo_url="https://e/z", install_status="installed")
    account = Account(name="c", broker_type="alpaca", environment="paper",
                      credentials="{}", supported_asset_types=["equities"], pdt_mode="off")
    worker = Worker(name="w3")
    db_session.add_all([algo, account, worker]); await db_session.flush()
    inst = AlgorithmInstance(algorithm_id=algo.id, account_id=account.id, worker_id=worker.id, status="running")
    db_session.add(inst); await db_session.commit()

    from coordinator.api.dependencies import get_container
    feeder = ParallelBacktestFeeder(instance_id=inst.id, session_factory=get_container().session_factory)

    from sdk.signals import Signal, SignalLeg, SignalType
    signals = [
        Signal(legs=[SignalLeg(symbol="SPY", signal_type=SignalType.BUY, quantity=10, asset_type="equities")]),
        Signal(legs=[SignalLeg(symbol="QQQ", signal_type=SignalType.SELL, quantity=5, asset_type="equities")]),
    ]
    await feeder.on_signals_emitted_async(datetime(2024, 3, 1, tzinfo=timezone.utc), signals)

    from sqlalchemy import select
    rows = (await db_session.execute(
        select(DecisionLog).where(DecisionLog.instance_id == inst.id)
                          .where(DecisionLog.mode == "backtest")
    )).scalars().all()
    assert len(rows) == 2
    symbols = {r.signals_produced[0]["legs"][0]["symbol"] for r in rows}
    assert symbols == {"SPY", "QQQ"}


@pytest.mark.asyncio
async def test_feeder_noop_methods_do_not_raise(test_app, db_session):
    """No-op observer methods should not raise."""
    from coordinator.api.dependencies import get_container
    feeder = ParallelBacktestFeeder(instance_id="fake-id", session_factory=get_container().session_factory)

    from sdk.signals import Signal, SignalLeg, SignalType
    sig = Signal(legs=[SignalLeg(symbol="X", signal_type=SignalType.BUY, quantity=1)])

    feeder.on_tick(datetime.now(timezone.utc), {})
    feeder.on_signal_rejected(datetime.now(timezone.utc), sig, "test")
    feeder.on_equity_point(datetime.now(timezone.utc), 100_000.0, 100_000.0, [])
    feeder.on_error(ValueError("test error"))
    # on_complete takes a summary object — pass None for the no-op check
    feeder.on_complete(None)
