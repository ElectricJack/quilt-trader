from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional


@dataclass
class PDTResult:
    approved: bool
    would_be_day_trade: bool
    day_trade_count: int
    warning: bool = False
    reason: Optional[str] = None


class PDTMonitor:
    ROLLING_WINDOW_DAYS = 5

    def check_signal(self, pdt_mode: str, existing_day_trades: list[dict],
                     signal_legs: list[dict], open_positions: dict, today: date) -> PDTResult:
        if pdt_mode == "off":
            return PDTResult(approved=True, would_be_day_trade=False, day_trade_count=0)

        from coordinator.services.asset_services import get_default_registry
        registry = get_default_registry()
        would_be_day_trade = False
        for leg in signal_legs:
            if registry.get_service(leg["symbol"]).is_pdt_exempt():
                continue
            symbol = leg["symbol"]
            side = leg["side"]
            if side in ("sell", "sell_short", "buy_to_cover"):
                pos = open_positions.get(symbol)
                if pos and pos.get("opened_today"):
                    would_be_day_trade = True
                    break

        window_start = today - timedelta(days=self.ROLLING_WINDOW_DAYS)
        recent_trades = [dt for dt in existing_day_trades if dt["day_trade_date"] > window_start]
        count = len(recent_trades)
        if would_be_day_trade:
            count += 1

        if pdt_mode == "block" and would_be_day_trade and count >= 4:
            return PDTResult(approved=False, would_be_day_trade=True, day_trade_count=count,
                           warning=True, reason="PDT limit reached")

        warning = would_be_day_trade and count >= 3
        return PDTResult(approved=True, would_be_day_trade=would_be_day_trade,
                        day_trade_count=count, warning=warning)
