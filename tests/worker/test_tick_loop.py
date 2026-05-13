import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from sdk.signals import Signal, SignalType
from worker.tick_loop import TickProcessor, TickResult
from worker.broker_adapter import MockBrokerAdapter
from worker.runner import AlgorithmRunner


class SimpleAlgo:
    def __init__(self):
        self._signals = []
    def on_start(self, config, restored_state): pass
    def on_tick(self, ctx): return list(self._signals)
    def on_stop(self): return {}
    def save_state(self): return {}
    def on_signal_rejected(self, signal, reason): self.last_rejection = (signal, reason)
    def on_trade_executed(self, signal, fill): self.last_fill = (signal, fill)
    def notify(self, event_name, message, data=None): pass
    def drain_notifications(self): return []


@pytest.fixture
def broker():
    b = MockBrokerAdapter()
    b.set_fill_price(150.0)
    b.set_account_info(cash=50000.0, portfolio_value=75000.0, buying_power=100000.0)
    return b


@pytest.fixture
def coordinator_client():
    client = AsyncMock()
    client.request_signal_approval.return_value = {"approved": True}
    return client


@pytest.fixture
def data_client():
    return AsyncMock()


@pytest.mark.asyncio
async def test_tick_no_signals(broker, coordinator_client, data_client):
    algo = SimpleAlgo()
    runner = AlgorithmRunner(instance_id="inst-1", algorithm=algo, config={}, restored_state=None)
    runner.start()
    processor = TickProcessor(runner=runner, broker=broker, data_client=data_client, coordinator_client=coordinator_client)
    result = await processor.process_tick(datetime.now(timezone.utc))
    assert isinstance(result, TickResult)
    assert result.signals_produced == 0
    assert result.trades_executed == 0
    assert result.trades_rejected == 0


@pytest.mark.asyncio
async def test_tick_with_approved_signal(broker, coordinator_client, data_client):
    algo = SimpleAlgo()
    algo._signals = [Signal.simple("AAPL", SignalType.BUY, 100)]
    runner = AlgorithmRunner(instance_id="inst-1", algorithm=algo, config={}, restored_state=None)
    runner.start()
    processor = TickProcessor(runner=runner, broker=broker, data_client=data_client, coordinator_client=coordinator_client)
    result = await processor.process_tick(datetime.now(timezone.utc))
    assert result.signals_produced == 1
    assert result.trades_executed == 1
    assert result.trades_rejected == 0
    coordinator_client.request_signal_approval.assert_called_once()


@pytest.mark.asyncio
async def test_tick_with_rejected_signal(broker, coordinator_client, data_client):
    coordinator_client.request_signal_approval.return_value = {"approved": False, "reason": "PDT limit reached"}
    algo = SimpleAlgo()
    algo._signals = [Signal.simple("AAPL", SignalType.SELL, 50)]
    runner = AlgorithmRunner(instance_id="inst-1", algorithm=algo, config={}, restored_state=None)
    runner.start()
    processor = TickProcessor(runner=runner, broker=broker, data_client=data_client, coordinator_client=coordinator_client)
    result = await processor.process_tick(datetime.now(timezone.utc))
    assert result.signals_produced == 1
    assert result.trades_executed == 0
    assert result.trades_rejected == 1
    assert algo.last_rejection[1] == "PDT limit reached"


@pytest.mark.asyncio
async def test_tick_builds_decision_log(broker, coordinator_client, data_client):
    algo = SimpleAlgo()
    algo._signals = [Signal.simple("AAPL", SignalType.BUY, 100, reasoning="Momentum")]
    runner = AlgorithmRunner(instance_id="inst-1", algorithm=algo, config={}, restored_state=None)
    runner.start()
    processor = TickProcessor(runner=runner, broker=broker, data_client=data_client, coordinator_client=coordinator_client)
    result = await processor.process_tick(datetime.now(timezone.utc))
    assert result.decision_log is not None
    assert result.decision_log["signals_produced"] is not None
    assert len(result.decision_log["signals_produced"]) == 1


@pytest.mark.asyncio
async def test_tick_multiple_signals(broker, coordinator_client, data_client):
    coordinator_client.request_signal_approval.side_effect = [
        {"approved": True}, {"approved": False, "reason": "PDT"}]
    algo = SimpleAlgo()
    algo._signals = [Signal.simple("AAPL", SignalType.BUY, 100), Signal.simple("TSLA", SignalType.BUY, 50)]
    runner = AlgorithmRunner(instance_id="inst-1", algorithm=algo, config={}, restored_state=None)
    runner.start()
    processor = TickProcessor(runner=runner, broker=broker, data_client=data_client, coordinator_client=coordinator_client)
    result = await processor.process_tick(datetime.now(timezone.utc))
    assert result.signals_produced == 2
    assert result.trades_executed == 1
    assert result.trades_rejected == 1
