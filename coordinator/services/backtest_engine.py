from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ComparisonResult:
    total_ticks: int
    matching_ticks: int
    match_percentage: float
    divergences: list[dict] = field(default_factory=list)
    exceeds_threshold: bool = False


class BacktestComparator:
    @staticmethod
    def compare(live_decisions: list[dict], backtest_decisions: list[dict], threshold: float = 5.0) -> ComparisonResult:
        if not live_decisions and not backtest_decisions:
            return ComparisonResult(total_ticks=0, matching_ticks=0, match_percentage=100.0)

        bt_by_ts = {d["timestamp"]: d for d in backtest_decisions}
        total = len(live_decisions)
        matching = 0
        divergences = []

        for live in live_decisions:
            ts = live["timestamp"]
            bt = bt_by_ts.get(ts)
            if bt is None:
                divergences.append({"timestamp": ts, "live_signals": live.get("signals_produced", []),
                                   "backtest_signals": None, "reason": "No backtest tick for this timestamp"})
                continue
            live_sigs = live.get("signals_produced", [])
            bt_sigs = bt.get("signals_produced", [])
            if BacktestComparator._signals_match(live_sigs, bt_sigs):
                matching += 1
            else:
                divergences.append({"timestamp": ts, "live_signals": live_sigs,
                                   "backtest_signals": bt_sigs, "reason": "Signal mismatch"})

        pct = (matching / total * 100) if total > 0 else 100.0
        divergence_pct = 100 - pct
        return ComparisonResult(total_ticks=total, matching_ticks=matching,
                               match_percentage=round(pct, 2), divergences=divergences,
                               exceeds_threshold=divergence_pct > threshold)

    @staticmethod
    def _signals_match(live: list, backtest: list) -> bool:
        if len(live) != len(backtest):
            return False
        if not live and not backtest:
            return True
        return BacktestComparator._signal_key(live) == BacktestComparator._signal_key(backtest)

    @staticmethod
    def _signal_key(signals: list) -> frozenset:
        keys = set()
        for sig in signals:
            for leg in sig.get("legs", []):
                keys.add((leg.get("symbol"), leg.get("signal_type")))
        return frozenset(keys)
