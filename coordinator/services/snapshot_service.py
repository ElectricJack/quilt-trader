from datetime import datetime, timezone
from typing import Optional


class SnapshotService:
    @staticmethod
    def compute_twr(snapshots: list[dict], cash_flows: list[dict]) -> float:
        if len(snapshots) < 2:
            return 0.0

        cf_by_ts = {}
        for cf in cash_flows:
            ts = cf["timestamp"]
            cf_by_ts[ts] = cf_by_ts.get(ts, 0) + cf["amount"]

        chain = 1.0
        for i in range(1, len(snapshots)):
            prev_value = snapshots[i - 1]["total_value"]
            curr_value = snapshots[i]["total_value"]
            cf_at_curr = cf_by_ts.get(snapshots[i]["timestamp"], 0)

            if prev_value == 0:
                continue

            if cf_at_curr != 0:
                period_return = curr_value / prev_value
                chain *= period_return
                snapshots[i]["_adjusted_start"] = curr_value + cf_at_curr
            else:
                prev_adjusted = snapshots[i - 1].get("_adjusted_start")
                start = prev_adjusted if prev_adjusted else prev_value
                if start == 0:
                    continue
                period_return = curr_value / start
                chain *= period_return

        return round((chain - 1) * 100, 2)

    @staticmethod
    def create_snapshot_data(
        account_id: str, total_value: float, cash: float,
        positions_value: float, net_deposits: float, source: str,
    ) -> dict:
        return {
            "account_id": account_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_value": total_value,
            "cash": cash,
            "positions_value": positions_value,
            "net_deposits_cumulative": net_deposits,
            "source": source,
        }
