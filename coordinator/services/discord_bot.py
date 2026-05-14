import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)
SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2, "critical": 3}


def format_trade_event(payload: dict) -> str:
    symbol = payload.get("symbol", "?")
    side = payload.get("side", "?")
    qty = payload.get("quantity", 0)
    price = payload.get("filled_price", 0)
    fees = payload.get("fees", 0)
    pnl = payload.get("pnl")
    msg = f"**Trade Executed** | {side.upper()} {qty} {symbol} @ ${price:.2f} | Fees: ${fees:.2f}"
    if pnl is not None:
        msg += f" | P/L: ${pnl:+.2f}"
    return msg


def format_algo_event(payload: dict) -> str:
    name = payload.get("algorithm_name", "?")
    account = payload.get("account_name", "?")
    old = payload.get("old_status", "?")
    new = payload.get("new_status", "?")
    return f"**Algorithm Status** | {name} on {account}: {old} → {new}"


def format_pdt_event(payload: dict) -> str:
    account = payload.get("account_name", "?")
    count = payload.get("day_trade_count", 0)
    remaining = payload.get("remaining", 0)
    return f"**PDT Warning** | {account}: {count} day trades in 5 days ({remaining} remaining)"


@dataclass
class RouteConfig:
    channel: str
    min_severity: str = "info"
    enabled: bool = True


class DiscordNotifier:
    def __init__(self) -> None:
        self._routes: dict[str, RouteConfig] = {}

    def set_route(self, event_type: str, channel: str, min_severity: str = "info") -> None:
        self._routes[event_type] = RouteConfig(channel=channel, min_severity=min_severity)

    def disable_route(self, event_type: str) -> None:
        if event_type in self._routes:
            self._routes[event_type].enabled = False

    def get_channel(self, event_type: str) -> Optional[str]:
        route = self._routes.get(event_type)
        if route and route.enabled:
            return route.channel
        return None

    def should_send(self, event_type: str, severity: str) -> bool:
        route = self._routes.get(event_type)
        if not route or not route.enabled:
            return False
        return SEVERITY_ORDER.get(severity, 0) >= SEVERITY_ORDER.get(route.min_severity, 0)
