import pytest
from unittest.mock import MagicMock
from sdk.signals import Signal, SignalType
from worker.runner import AlgorithmRunner, RunnerState


class FakeAlgorithm:
    def __init__(self):
        self.started = False
        self.stopped = False
        self.tick_count = 0
        self.config = None
        self.restored_state = None
        self._signals = []

    def on_start(self, config, restored_state):
        self.started = True
        self.config = config
        self.restored_state = restored_state

    def on_tick(self, ctx):
        self.tick_count += 1
        return list(self._signals)

    def on_stop(self):
        self.stopped = True
        return {"tick_count": self.tick_count}

    def save_state(self):
        return {"tick_count": self.tick_count}

    def set_signals(self, signals):
        self._signals = signals

    def on_signal_rejected(self, signal, reason):
        pass

    def on_trade_executed(self, signal, fill):
        pass


def test_runner_initial_state():
    runner = AlgorithmRunner(instance_id="inst-1", algorithm=FakeAlgorithm(), config={"risk": 0.02}, restored_state=None)
    assert runner.state == RunnerState.STOPPED


def test_runner_start():
    algo = FakeAlgorithm()
    runner = AlgorithmRunner(instance_id="inst-1", algorithm=algo, config={"risk": 0.02}, restored_state={"tick_count": 5})
    runner.start()
    assert runner.state == RunnerState.RUNNING
    assert algo.started is True
    assert algo.config == {"risk": 0.02}
    assert algo.restored_state == {"tick_count": 5}


def test_runner_stop():
    algo = FakeAlgorithm()
    runner = AlgorithmRunner(instance_id="inst-1", algorithm=algo, config={}, restored_state=None)
    runner.start()
    final_state = runner.stop()
    assert runner.state == RunnerState.STOPPED
    assert algo.stopped is True
    assert final_state == {"tick_count": 0}


def test_runner_tick_no_signals():
    algo = FakeAlgorithm()
    runner = AlgorithmRunner(instance_id="inst-1", algorithm=algo, config={}, restored_state=None)
    runner.start()
    signals = runner.tick(MagicMock())
    assert signals == []
    assert algo.tick_count == 1


def test_runner_tick_with_signals():
    algo = FakeAlgorithm()
    signal = Signal.simple("AAPL", SignalType.BUY, 100, reasoning="Test buy")
    algo.set_signals([signal])
    runner = AlgorithmRunner(instance_id="inst-1", algorithm=algo, config={}, restored_state=None)
    runner.start()
    signals = runner.tick(MagicMock())
    assert len(signals) == 1
    assert signals[0].legs[0].symbol == "AAPL"


def test_runner_save_state():
    algo = FakeAlgorithm()
    runner = AlgorithmRunner(instance_id="inst-1", algorithm=algo, config={}, restored_state=None)
    runner.start()
    runner.tick(MagicMock())
    runner.tick(MagicMock())
    state = runner.save_state()
    assert state == {"tick_count": 2}


def test_runner_tick_while_stopped_raises():
    algo = FakeAlgorithm()
    runner = AlgorithmRunner(instance_id="inst-1", algorithm=algo, config={}, restored_state=None)
    with pytest.raises(RuntimeError, match="not running"):
        runner.tick(MagicMock())
