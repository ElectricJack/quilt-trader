"""QuiltTrader SDK — contract for trading algorithms and scrapers."""

from sdk.algorithm import QuiltAlgorithm
from sdk.scraper import QuiltScraper
from sdk.context import TickContext
from sdk.signals import Signal, SignalLeg, SignalType, OrderType
from sdk.models import Position, TradeFill, OptionChain, OptionContract
from sdk.manifest import QuiltManifest, ManifestError

__all__ = [
    "QuiltAlgorithm",
    "QuiltScraper",
    "TickContext",
    "Signal",
    "SignalLeg",
    "SignalType",
    "OrderType",
    "Position",
    "TradeFill",
    "OptionChain",
    "OptionContract",
    "QuiltManifest",
    "ManifestError",
]
