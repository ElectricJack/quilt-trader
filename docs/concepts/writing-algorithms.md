# Writing Algorithms

> A Quilt algorithm is a Python class that decides what to trade on each tick. The framework handles data, execution, state, and the boring parts.

## What you'll learn

- The lifecycle of a `QuiltAlgorithm` from start to stop.
- The supported trigger formats and how to pick one.
- The `TickContext` API — what your algorithm can ask the framework for.
- How to construct and emit signals.
- How state persistence works across worker restarts.

## The problem this solves

Naive algo-trading frameworks make you re-invent the same five things every time. You write your own data plumbing (download bars, cache them, deal with gaps). You write your own broker adapter (different per broker, all subtly different). You write a state-recovery dance so a power outage at 09:31 doesn't leave you in an unknown position. You write a scheduler so something runs every minute during market hours but not on holidays. You write notifications. By the time you start writing the actual strategy you're three weeks in and most of your bugs are in plumbing, not signal.

Quilt's algorithm SDK is intentionally tiny. There is a base class with three methods you must implement (`on_start`, `on_tick`, `on_stop`) and three you may implement (`save_state`, `on_signal_rejected`, `on_trade_executed`). There is a context object that hands you positions, cash, and recent bars. There is a `Signal` dataclass that describes one or more legs of an order. There is a YAML manifest that declares what you trade, what you need, and when to fire. That is the entire surface. Everything else — data fetching, broker routing, execution, retries, state recovery on restart, holiday-aware scheduling, notifications — is the framework's job. You write a strategy; the framework runs it.

## How Quilt does it

### The manifest (`quilt.yaml`)

Each algorithm package has a `quilt.yaml` at its root. The schema is parsed by `QuiltManifest._parse` in `sdk/manifest.py:91`. Here is a representative algorithm manifest (taken from `data/packages/stock-ema-crossover/quilt.yaml`):

```yaml
name: stock-ema-crossover
type: algorithm
version: 1.0.0
description: EMA crossover on QQQ - holds TQQQ when bullish, SHY when bearish
entry_point: algorithm.py
class_name: StockEmaCrossover
trigger: bar:1day
requirements:
  asset_types:
    - equities
config:
  parameters:
    - name: eval_symbol
      type: string
      default: QQQ
    - name: ema_short
      type: integer
      default: 10
    - name: ema_long
      type: integer
      default: 100
assets:
  - symbol: QQQ
    asset_class: equities
    timeframe: 1day
    source: polygon
  - symbol: TQQQ
    asset_class: equities
    timeframe: 1day
    source: polygon
```

Required fields for an algorithm manifest:

| Field | Purpose | Reference |
| --- | --- | --- |
| `name` | Package identifier | `sdk/manifest.py:92` |
| `type` | Must be `algorithm` (or `scraper`) | `sdk/manifest.py:96` |
| `version` | SemVer string | `sdk/manifest.py:241` |
| `entry_point` | Path to the Python file inside the package | `sdk/manifest.py:100` |
| `class_name` | Name of the `QuiltAlgorithm` subclass | `sdk/manifest.py:102` |
| `requirements.asset_types` | Subset of `{equities, options, crypto, index}` | `sdk/manifest.py:104` |
| `trigger` | When `on_tick` fires (see below) | `sdk/manifest.py:73` |

Optional:

- `market_timezone` — IANA timezone (e.g. `America/New_York`). If omitted, the parser picks a default per `requirements.asset_types`: equities or options → `America/New_York`; crypto-only → `UTC`; anything else → `UTC`. See `_default_market_timezone` at `sdk/manifest.py:22`. This timezone controls what `ctx.market_time()` returns and which calendar `ctx.is_market_open()` uses.
- `config.parameters` — typed parameter list with defaults. Values flow into your `on_start(config, ...)`.
- `assets` — symbols you trade. Each entry is validated against an asset registry (`sdk/manifest.py:170`); the broker is decided by the deployment's account, not the manifest.
- `requirements.options_level`, `requirements.account_features`, `requirements.brokers`, `requirements.data_dependencies` — capability gates checked at deploy time.

### The class

Your entry point file defines a class that extends `QuiltAlgorithm` (`sdk/algorithm.py:11`). Seven overridable callbacks (three required, four optional) plus a `notify(...)` helper.

**`on_start(self, config, restored_state)`** — `sdk/algorithm.py:17`. Called once when the algorithm instance comes up. `config` is the parsed `config.parameters` from the manifest, with any deployment-time overrides applied. `restored_state` is either `None` (cold start) or the dict your last `save_state()` returned. Put initialization here: read config into attributes, restore prior state, set up internal counters. Do not place expensive network or disk work here; it blocks the worker startup.

**`on_tick(self, ctx)`** — `sdk/algorithm.py:20`. The hot path. Called every time the trigger fires. Return a `list[Signal]` — empty is fine. Everything you can ask the framework is on `ctx`. This method runs synchronously in the worker subprocess; long-running work delays the next tick.

**`on_stop(self)`** — `sdk/algorithm.py:23`. Called once when the instance is shut down (deploy update, manual stop, worker restart). Return a dict of final state. Most implementations just return `self.save_state()`.

**`save_state(self)`** — `sdk/algorithm.py:26`. Called by the framework after every tick (best-effort; failures are logged and swallowed so a bad checkpoint cannot crash the worker — see `worker/live_instance_runtime.py:170`). Return a JSON-serializable dict. The framework stores it durably; it is what gets handed back to `on_start` as `restored_state` on the next boot.

**`on_signal_rejected(self, signal, reason)`** — `sdk/algorithm.py:29`. Optional. Called if the coordinator (risk checks, broker, PDT logic) refuses a signal you emitted. Use it to log, retry, or back off. Default is a no-op.

**`on_trade_executed(self, signal, fill)`** — `sdk/algorithm.py:32`. Optional. Called after a fill is confirmed. `fill` is a `TradeFill` (`sdk/models.py:9`) with filled price, fees, and slippage. Useful for tracking actual cost basis or computing realized P&L.

**`on_position_closed(self, symbol, reason, details=None)`** — `sdk/algorithm.py:35`. Optional. Called when a user (or external workflow) manually closes a position you own. Use it to drop the symbol from any internal tracking maps.

**`notify(self, event_name, message, data=None)`** — `sdk/algorithm.py:39`. Helper, not a callback. Queues a notification (Discord, dashboard) that the framework drains after the tick. Use it for material events like "regime change detected" or "stop hit"; don't spam it every tick.

### The tick context

`TickContext` is an abstract base in `sdk/context.py:12`. The worker provides one implementation for live trading and the backtest engine provides another. Your algorithm code does not care which; the API is the same.

| Attribute / method | What it gives you | Reference |
| --- | --- | --- |
| `ctx.timestamp` | Current sim time as a `datetime` (naive UTC by convention in backtests) | `sdk/context.py:21` |
| `ctx.mode` | `"live"`, `"paper"`, or `"backtest"` | `sdk/context.py:26` |
| `ctx.positions` | `dict[symbol, Position]` of what you currently hold | `sdk/context.py:31` |
| `ctx.account_value` | Total account equity (cash + market value of positions) | `sdk/context.py:36` |
| `ctx.cash` | Settled cash available | `sdk/context.py:41` |
| `ctx.buying_power` | Cash plus margin, broker-dependent | `sdk/context.py:46` |
| `ctx.market_data(symbol, timeframe="1min", bars=100, source=None)` | Recent OHLCV bars as a `pandas.DataFrame` | `sdk/context.py:50` |
| `ctx.market_time()` | Current sim time converted to the manifest's `market_timezone` (tz-aware) | `sdk/context.py:54` |
| `ctx.is_market_open()` | `True` during the regular session for your manifest's asset types (NYSE calendar for equities/options, always-on for crypto) | `sdk/context.py:64` |
| `ctx.data(source_name)` | Read a scraper or custom data source by name | `sdk/context.py:76` |
| `ctx.option_chain(symbol, expiration=None)` | `OptionChain` for the symbol | `sdk/context.py:80` |
| `ctx.dataset(name, ...)` | Bitemporal dataset filtered to what was knowable as-of the sim clock | `sdk/context.py:83` |

`Position` is defined at `sdk/models.py:63` and has `quantity`, `avg_cost`, `current_price`, `market_value`, `unrealized_pnl`, and `asset_type`.

### Triggers

The `trigger` field controls when `on_tick` fires. It is validated against the regex at `sdk/manifest.py:11`:

```
^(bar:[a-z0-9]+|event|interval:\d+[smh])$
```

| Trigger | Fires when | When to use |
| --- | --- | --- |
| `bar:1min` | A 1-minute bar closes for any subscribed asset | Default for short-timeframe equities and options |
| `bar:1h`, `bar:1day` | The named bar closes | Slower strategies that only act on session or daily data |
| `interval:30s`, `interval:5m`, `interval:1h` | Every N seconds, minutes, or hours, regardless of bar boundaries | Quote-driven, rebalancing, or lower-frequency polling on wall-clock cadence |
| `event` | An external event (scraper completion, custom event) | Event-driven strategies that wake on news, not the clock |

If the field is omitted, the default is `bar:1min` (`sdk/manifest.py:73`).

### Signals

A `Signal` (`sdk/signals.py:83`) is a list of `SignalLeg` (`sdk/signals.py:43`) plus optional pricing limits and reasoning. For single-leg orders use the `Signal.simple` classmethod (`sdk/signals.py:96`):

```python
from sdk.signals import Signal, SignalType, OrderType

# Buy 100 shares of AAPL at market.
signal = Signal.simple(
    symbol="AAPL",
    signal_type=SignalType.BUY,
    quantity=100,
    asset_type="equities",
    order_type=OrderType.MARKET,
    reasoning="20-EMA crossed above 50-EMA",
)
```

For multi-leg orders (spreads, condors, etc.) construct legs directly. The leg symbols below use OCC option symbol format (root + expiry + call/put + strike):

```python
from sdk.signals import Signal, SignalLeg, SignalType, OrderType

signal = Signal(
    legs=[
        SignalLeg(symbol="AAPL  240621C00200000", signal_type=SignalType.BUY,
                  quantity=1, asset_type="options", order_type=OrderType.LIMIT,
                  limit_price=2.50),
        SignalLeg(symbol="AAPL  240621C00210000", signal_type=SignalType.SELL,
                  quantity=1, asset_type="options", order_type=OrderType.LIMIT,
                  limit_price=1.20),
    ],
    strategy_type="vertical_spread",
    net_debit_limit=1.40,
    reasoning="Bull call spread on EMA cross",
)
```

The enums:

- `SignalType` (`sdk/signals.py:8`): `BUY`, `SELL`, `SELL_SHORT`, `BUY_TO_COVER`.
- `OrderType` (`sdk/signals.py:15`): `MARKET`, `LIMIT`, `STOP`, `STOP_LIMIT`.
- `TimeInForce` (`sdk/signals.py:22`): `DAY`, `GTC`, `IOC`.

Asset type on each leg must be one of `equities`, `options`, `crypto`, `index` (validated at `sdk/signals.py:34`).

### State persistence

Algorithms restart. A deployment update redeploys the package; a worker crashes; a Pi loses power at 03:00. Without persistence you would lose internal counters, regime flags, EMA values, and any other accumulated state at the worst possible moment.

The flow is simple:

1. After every tick, the framework calls `save_state()` on your algorithm. You return a JSON-serializable dict.
2. The coordinator stores it durably (alongside the instance row).
3. On the next boot, the framework calls `on_start(config, restored_state=<the dict>)`.
4. Your `on_start` checks whether `restored_state` is `None` (cold start) or populated (recovery) and rehydrates accordingly.

What goes in the dict: anything you cannot reconstruct from current bars + positions. Regime flags (`"current_state": "up"`), tracking counters (`"trades_today": 4`), the last value you notified on (`"last_notified_drawdown": -0.08`). What does *not* belong: positions (`ctx.positions` is the source of truth), cash (`ctx.cash`), bars (re-fetch with `ctx.market_data`).

## Worked example

A complete EMA-crossover algorithm. Drop this in `my-algo/algorithm.py` alongside a `quilt.yaml` declaring `entry_point: algorithm.py`, `class_name: EmaCross`, and a `trigger: bar:1day`.

```python
from typing import TYPE_CHECKING

from sdk.algorithm import QuiltAlgorithm
from sdk.signals import Signal, SignalType, OrderType

if TYPE_CHECKING:
    from sdk.context import TickContext


class EmaCross(QuiltAlgorithm):
    """Hold the symbol when short EMA > long EMA, exit when it crosses back."""

    def on_start(self, config: dict, restored_state: dict | None) -> None:
        self.symbol = config.get("symbol", "QQQ")
        self.ema_short = int(config.get("ema_short", 10))
        self.ema_long = int(config.get("ema_long", 50))
        self.pct_invest = float(config.get("pct_invest", 0.95))
        # Track whether we last saw a bullish or bearish cross. Survives restarts.
        self.in_position = (restored_state or {}).get("in_position", False)

    def on_tick(self, ctx: "TickContext") -> list[Signal]:
        # Pull enough history to make the long EMA reliable.
        bars = ctx.market_data(self.symbol, "1day", self.ema_long * 2)
        if bars is None or len(bars) < self.ema_long:
            return []

        close = bars["close"]
        short = close.ewm(span=self.ema_short, adjust=False).mean().iloc[-1]
        long_ = close.ewm(span=self.ema_long, adjust=False).mean().iloc[-1]
        price = close.iloc[-1]

        signals: list[Signal] = []

        if short > long_ and not self.in_position:
            qty = int(ctx.account_value * self.pct_invest / price)
            if qty > 0:
                signals.append(Signal.simple(
                    self.symbol, SignalType.BUY, qty,
                    asset_type="equities", order_type=OrderType.MARKET,
                    reasoning=f"short EMA {short:.2f} > long EMA {long_:.2f}",
                ))
                self.in_position = True

        elif short < long_ and self.in_position:
            pos = ctx.positions.get(self.symbol)
            if pos and pos.quantity > 0:
                signals.append(Signal.simple(
                    self.symbol, SignalType.SELL, pos.quantity,
                    asset_type="equities", order_type=OrderType.MARKET,
                    reasoning=f"short EMA {short:.2f} < long EMA {long_:.2f}",
                ))
            self.in_position = False

        return signals

    def save_state(self) -> dict:
        return {"in_position": self.in_position}

    def on_stop(self) -> dict:
        return self.save_state()
```

That is the whole algorithm. The manifest declares the trigger, the SDK delivers the bars, the framework routes the signals to whatever broker your deployment is wired to.

To run this against historical data or deploy it to a live account, see [`cli-and-agentic-workflows.md`](./cli-and-agentic-workflows.md).

## Limits and sharp edges

- **`on_tick` is synchronous and blocking.** It runs in the worker subprocess. Anything that takes seconds — a slow scraper read, an HTTP call to an external API, heavy numerical work — delays the next tick. If you need to do expensive work, do it in a scraper package (separate process, scheduled) and read the result via `ctx.data(source_name)`.
- **No `async` / `await`.** The SDK is synchronous. Do not define `async def on_tick`; it will not be awaited.
- **`ctx.market_data` returns recent bars only.** It is sized for "what do I need to make the next decision," not "give me ten years of history." For research over long windows, run the algorithm under the backtest engine instead of trying to slurp years of bars at tick time.
- **State must be JSON-serializable.** `save_state` returns a dict that gets serialized through JSON. `datetime`, `Decimal`, custom classes — convert them yourself (ISO strings, floats, dicts).
- **The manifest's `assets` list is authoritative for data subscription.** Symbols you call `ctx.market_data` on without listing in `assets` may not have bars available in live mode.
- **`on_start` runs every boot, including recoveries.** Idempotency matters: do not create resources you cannot recreate, and always handle the `restored_state is None` cold-start case.

## See also

- [`data-collection.md`](./data-collection.md) — what `ctx.market_data` and `ctx.data` are pulling from, and how to register a scraper as a data source.
- [`backtest-accuracy.md`](./backtest-accuracy.md) — running the same algorithm class against history; what the backtest engine simulates faithfully and what it does not.
- [`cli-and-agentic-workflows.md`](./cli-and-agentic-workflows.md) — installing, deploying, and iterating on a package from the CLI.
