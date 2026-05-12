# QuiltTrader — Design Specification

**Date:** 2026-05-12
**Status:** Draft
**Repo:** https://github.com/ElectricJack/quilt-trader

## 1. Overview

QuiltTrader is an algorithmic trading framework designed to run on one or more Raspberry Pi nodes. It provides centralized orchestration, monitoring, and management of multiple trading algorithms across multiple brokerage accounts, with a web dashboard and Discord bot interface.

### 1.1 Goals

- Run and monitor multiple trading algorithms across multiple Raspberry Pis
- Support multiple brokers and account types (equities, options, crypto, futures)
- Provide a rich web dashboard for account management, algorithm lifecycle, manual trading, and performance analysis
- Enable live/backtest comparison to detect data divergence and lookahead bias
- Log every decision and trade at granular detail for future analysis (including by AI agents)
- Manage data pipelines (market data downloads, custom scrapers) centrally
- Provide Discord bot integration for notifications and remote management
- Persist algorithm state across restarts and power loss

### 1.2 Non-Goals (for initial version)

- High-frequency trading (sub-second latency is not a requirement)
- Mobile app (web dashboard accessed via browser is sufficient)
- Multi-user authentication (single-user system)
- Cloud deployment (designed for local Raspberry Pi hardware on Tailscale)

---

## 2. System Architecture

### 2.1 Topology: Hub-and-Spoke

```
Coordinator (Pi or dedicated machine)
├── FastAPI backend (REST + WebSocket)
├── React dashboard (Vite, served by FastAPI)
├── Discord bot
├── SQLite database
├── Scheduler (nightly backtests, archival, scraper cron)
├── Data layer
│   ├── Market data cache (Polygon, etc.)
│   └── Scraper runners (alpha-picks-scraper, etc.)
└── GitHub integration (PAT-based, clone/update packages)

Worker Pi A                        Worker Pi B
├── Worker agent                   ├── Worker agent
├── Algo instance 1 (Alpaca)       └── Algo instance 3 (Tradier)
└── Algo instance 2 (Alpaca)

Communication: Workers ←WebSocket over Tailscale→ Coordinator
Data requests: Workers ←REST over Tailscale→ Coordinator data API
```

### 2.2 Component Responsibilities

**Coordinator** — The single brain of the system. It:
- Owns all persistent state (SQLite database)
- Serves the web dashboard
- Runs the Discord bot
- Manages algorithm and scraper package installation from GitHub
- Runs all scrapers and manages all data (market data + custom scraper output)
- Schedules nightly backtest comparisons and data archival
- Receives events from workers and routes them (DB, Discord, dashboard WebSocket)
- Performs PDT checking before approving trade signals
- Handles manual trading commands from the dashboard (routed to a worker for execution — see Section 6.5)

**Worker Nodes** — Lightweight execution hosts. They:
- Run a worker agent process that maintains a WebSocket connection to the coordinator
- Execute algorithm instances as isolated subprocesses
- Fetch data from the coordinator's data API
- Place orders via Lumibot broker adapters (broker connections live on workers)
- Stream events, logs, and state checkpoints back to the coordinator
- Hold broker credentials in memory only (received from coordinator on algo start)

**Algorithm/Scraper Packages** — Separate GitHub repos containing trading algorithms or data scrapers, installed and managed by the coordinator.

### 2.3 Communication Protocol

Workers maintain a persistent WebSocket connection to the coordinator over Tailscale.

**Coordinator → Worker messages:**
- `start_algorithm` — Deploy and start an algorithm instance (includes config, restored state, broker credentials)
- `stop_algorithm` — Graceful shutdown, wait for state save
- `update_algorithm` — Pull latest code from GitHub, restart if running
- `force_stop_algorithm` — Immediate stop (for manual trading override)

**Worker → Coordinator messages:**
- `heartbeat` — Every 30 seconds, includes resource usage
- `signal_request` — Algorithm produced signals, requesting approval to execute
- `trade_executed` — Order filled, includes fill details (price, fees, slippage)
- `state_checkpoint` — Algorithm called `save_state()`
- `decision_log` — Tick-level decision data for logging
- `algo_event` — Custom events defined by the algorithm
- `algo_error` — Algorithm subprocess crashed or raised an unhandled exception
- `algo_stopped` — Algorithm shut down cleanly, includes final state

**Coordinator → Worker responses:**
- `signal_approved` — Execute the trade
- `signal_rejected` — PDT block or other rejection, includes reason

### 2.4 Deployment Flexibility

The coordinator and a worker can run on the same Pi. The worker agent connects to `localhost` instead of a Tailscale IP. This supports the single-Pi starting configuration where one device does everything.

---

## 3. Data Model

All data lives in a single SQLite database on the coordinator.

### 3.1 Accounts

Represents a brokerage account connected to the system.

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT (UUID) | Primary key |
| name | TEXT | User-friendly name (e.g., "Alpaca Main") |
| broker_type | TEXT | Broker identifier (alpaca, tradier, etc.) |
| credentials | TEXT (encrypted JSON) | Broker API keys, encrypted at rest |
| supported_asset_types | JSON | Array of supported types: equities, options, crypto, futures |
| options_level | INTEGER | Options approval level (0-4), null if options not supported |
| account_features | JSON | Array of features: margin, short_selling, extended_hours, etc. |
| pdt_mode | TEXT | "off", "warn", or "block" |
| locked_by | TEXT (FK) | Algorithm instance ID currently running, or null |
| created_at | DATETIME | |
| updated_at | DATETIME | |

### 3.2 Algorithms

An algorithm package installed from GitHub.

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT (UUID) | Primary key |
| repo_url | TEXT | GitHub repo URL |
| name | TEXT | From quilt.yaml |
| description | TEXT | From quilt.yaml |
| version | TEXT | From quilt.yaml |
| commit_hash | TEXT | Current installed commit |
| required_asset_types | JSON | From quilt.yaml requirements |
| required_options_level | INTEGER | Minimum options level needed, null if not needed |
| required_account_features | JSON | From quilt.yaml requirements |
| supported_brokers | JSON | Null means any broker |
| data_dependencies | JSON | Array of {name, repo} objects from quilt.yaml |
| config_schema | JSON | Parameter definitions from quilt.yaml |
| custom_events | JSON | Custom notification events from quilt.yaml |
| install_status | TEXT | "installed", "error", "updating" |
| install_error | TEXT | Error message if install failed |
| installed_at | DATETIME | |
| updated_at | DATETIME | |

### 3.3 Algorithm Instances

A specific algorithm assigned to a specific account on a specific worker.

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT (UUID) | Primary key |
| algorithm_id | TEXT (FK) | References algorithms.id |
| account_id | TEXT (FK) | References accounts.id |
| worker_id | TEXT (FK) | References workers.id |
| status | TEXT | "running", "stopped", "error", "disconnected" |
| config_values | JSON | User-configured parameter values |
| persisted_state | JSON | Last save_state() output |
| state_stale | BOOLEAN | True if manual trading occurred after last state save |
| started_at | DATETIME | Current run start time |
| stopped_at | DATETIME | Current run stop time |
| total_pnl_current_run | REAL | P/L since current run started |
| total_pnl_lifetime | REAL | P/L across all runs of this instance |
| total_fees_current_run | REAL | Fees since current run started |
| total_fees_lifetime | REAL | Fees across all runs |
| created_at | DATETIME | When instance was first created |
| updated_at | DATETIME | |

### 3.4 Workers

Registered worker Raspberry Pis.

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT (UUID) | Primary key |
| name | TEXT | User-friendly name (e.g., "Pi Living Room") |
| tailscale_ip | TEXT | Tailscale IP address |
| status | TEXT | "online", "offline" |
| last_heartbeat | DATETIME | |
| max_algorithms | INTEGER | Max concurrent algorithm subprocesses |
| created_at | DATETIME | |

### 3.5 Scrapers

Data scraper packages installed from GitHub.

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT (UUID) | Primary key |
| repo_url | TEXT | GitHub repo URL |
| name | TEXT | From quilt.yaml |
| description | TEXT | From quilt.yaml |
| version | TEXT | From quilt.yaml |
| commit_hash | TEXT | Current installed commit |
| schedule | TEXT | Cron expression from quilt.yaml |
| output_format | TEXT | csv, json, parquet, etc. |
| output_filename | TEXT | |
| status | TEXT | "running", "stopped", "error" |
| dependent_algorithm_count | INTEGER | Number of running algorithms depending on this |
| last_success | DATETIME | Last successful run |
| last_error | TEXT | Error message from last failure |
| installed_at | DATETIME | |
| updated_at | DATETIME | |

### 3.6 Trade Log

Every individual fill executed by the system. For multi-leg orders (options spreads, pairs trades), each leg is a separate row linked by `group_id`. P/L for multi-leg strategies is tracked at the position level (see 3.14 Positions), not here.

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT (UUID) | Primary key |
| group_id | TEXT (UUID) | Groups legs of a multi-leg order. Single-leg trades get their own unique group_id. |
| instance_id | TEXT (FK) | References algorithm_instances.id, null for manual trades |
| account_id | TEXT (FK) | References accounts.id |
| position_id | TEXT (FK) | References positions.id (links this fill to a tracked position) |
| source | TEXT | "algorithm" or "manual" |
| timestamp | DATETIME | When the trade was executed |
| symbol | TEXT | Ticker symbol |
| asset_type | TEXT | equities, options, crypto, futures |
| side | TEXT | "buy", "sell", "sell_short", "buy_to_cover" |
| quantity | REAL | Number of shares/contracts (supports fractional for crypto) |
| order_type | TEXT | "market", "limit", "stop", "stop_limit" |
| requested_price | REAL | Price at time of signal (null for market orders) |
| filled_price | REAL | Actual fill price |
| fees | REAL | Total fees (commissions + exchange + network) |
| fee_breakdown | JSON | Detailed fee components: {commission, exchange_fee, network_fee, maker_taker}. Optional — null for simple equity trades. |
| slippage | REAL | filled_price - requested_price (signed) |
| is_day_trade | BOOLEAN | Whether this constituted a day trade (always false for crypto) |
| metadata | JSON | Additional broker-specific fill details |

### 3.7 Decision Log

Every tick's decision data — the core of live/backtest comparison.

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT (UUID) | Primary key |
| instance_id | TEXT (FK) | References algorithm_instances.id |
| timestamp | DATETIME | Tick timestamp |
| mode | TEXT | "live" or "backtest" |
| tick_data | JSON | Snapshot of all data the algorithm received |
| signals_produced | JSON | Array of Signal objects returned by on_tick() |
| reasoning | JSON | Algorithm-provided reasoning/signal metadata |
| data_sources_used | JSON | Which data sources were consulted and their versions/timestamps |

**Indexes:** (instance_id, mode, timestamp) for efficient comparison queries.

### 3.8 Events

Event bus persistence layer.

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT (UUID) | Primary key |
| source_type | TEXT | "algorithm", "scraper", "system", "manual" |
| source_id | TEXT | Instance ID, scraper ID, or null for system events |
| event_type | TEXT | e.g., "trade_executed", "algo_error", "pdt_warning", custom events |
| severity | TEXT | "info", "warning", "error", "critical" |
| payload | JSON | Event-specific data |
| timestamp | DATETIME | |
| routed_to_discord | BOOLEAN | Whether this event was sent to Discord |
| discord_channel | TEXT | Which channel it was sent to, if applicable |

### 3.9 Data Sources

Registry of available data.

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT (UUID) | Primary key |
| type | TEXT | "market" or "custom" |
| source | TEXT | "polygon", "theta_data", or scraper name |
| name | TEXT | Identifier used in data API (e.g., "alpha-picks", "AAPL") |
| description | TEXT | |
| file_path | TEXT | Path to data file on coordinator filesystem |
| last_updated | DATETIME | |
| metadata | JSON | Additional info (date range for market data, row count, etc.) |

### 3.10 Backtest Comparisons

Results of nightly divergence checks.

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT (UUID) | Primary key |
| instance_id | TEXT (FK) | References algorithm_instances.id |
| algorithm_id | TEXT (FK) | References algorithms.id |
| time_range_start | DATETIME | Start of comparison window |
| time_range_end | DATETIME | End of comparison window |
| total_ticks | INTEGER | Number of ticks compared |
| matching_ticks | INTEGER | Ticks where live and backtest decisions matched |
| match_percentage | REAL | matching_ticks / total_ticks * 100 |
| divergences | JSON | Array of {timestamp, live_signal, backtest_signal, data_diff} |
| summary | TEXT | Human-readable summary of findings |
| created_at | DATETIME | |

### 3.11 PDT Tracking

Rolling day trade tracker per account.

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT (UUID) | Primary key |
| account_id | TEXT (FK) | References accounts.id |
| trade_id | TEXT (FK) | References trade_log.id |
| symbol | TEXT | |
| open_timestamp | DATETIME | When position was opened |
| close_timestamp | DATETIME | When position was closed (same day) |
| day_trade_date | DATE | The date this day trade occurred |

### 3.12 Market Data Downloads

Tracks market data download jobs (Polygon, Theta Data).

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT (UUID) | Primary key |
| symbols | JSON | Array of symbols being downloaded |
| date_range_start | DATE | |
| date_range_end | DATE | |
| provider | TEXT | "polygon" or "theta_data" |
| data_type | TEXT | "bars", "trades", "quotes" |
| timeframe | TEXT | "1min", "5min", "1hour", "1day" |
| status | TEXT | "queued", "downloading", "completed", "error" |
| progress_current | INTEGER | Number of symbol-days downloaded |
| progress_total | INTEGER | Total symbol-days to download |
| error_message | TEXT | |
| started_at | DATETIME | |
| completed_at | DATETIME | |

### 3.13 Data Archival

Tracks archived data batches.

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT (UUID) | Primary key |
| table_name | TEXT | "decision_log" or "trade_log" |
| date_range_start | DATETIME | |
| date_range_end | DATETIME | |
| row_count | INTEGER | Number of rows archived |
| file_path | TEXT | Path to Parquet file |
| file_size_bytes | INTEGER | |
| archived_at | DATETIME | |

### 3.14 Positions

Tracks open and closed composite positions. This is the source of truth for P/L calculations, especially for multi-leg strategies like options spreads and pairs trades. Single-leg equity/crypto trades also get a position entry for uniform P/L tracking.

| Column | Type | Description |
|--------|------|-------------|
| id | TEXT (UUID) | Primary key |
| instance_id | TEXT (FK) | References algorithm_instances.id, null for manual positions |
| account_id | TEXT (FK) | References accounts.id |
| strategy_type | TEXT | "single", "bull_call_spread", "bear_put_spread", "iron_condor", "straddle", "strangle", "pairs_trade", "custom" |
| legs | JSON | Array of leg details: [{symbol, side, quantity, avg_cost, asset_type}] |
| status | TEXT | "open", "closed", "expired", "partially_closed" |
| opened_at | DATETIME | When the position was first opened |
| closed_at | DATETIME | When fully closed/expired, null while open |
| open_group_id | TEXT (FK) | References trade_log.group_id for opening fills |
| close_group_id | TEXT (FK) | References trade_log.group_id for closing fills, null while open |
| net_cost | REAL | Net cost to open the position (debit = positive, credit = negative) |
| net_proceeds | REAL | Net proceeds on close (null while open) |
| net_pnl | REAL | Realized P/L for the entire position (all legs combined). Null while open. |
| unrealized_pnl | REAL | Current unrealized P/L (updated on each tick, null when closed) |
| total_fees | REAL | Sum of all fees across all legs (open + close) |
| adjustments | JSON | Funding rate payments, dividends, assignment/exercise events. Array of {timestamp, type, amount, description}. |
| metadata | JSON | Additional position-level data |

**Key behaviors:**
- When an algorithm returns a multi-leg Signal, the framework creates a single Position with all legs
- When a single-leg Signal is returned, a Position is still created (strategy_type: "single") for uniform tracking
- P/L is calculated at the position level: `net_pnl = net_proceeds - net_cost - total_fees + sum(adjustments)`
- The dashboard displays positions, not individual trade log rows, for P/L analysis
- The `adjustments` column handles crypto funding rates, dividend payments, and options assignment/exercise events that affect P/L but aren't trades
- Algorithm instance lifetime P/L is the sum of all position net_pnl values

---

## 4. Algorithm Package Contract

### 4.1 Repository Structure

Each algorithm lives in its own GitHub repository with the following structure:

```
my-algorithm/
├── quilt.yaml           # Required: package manifest
├── algorithm.py         # Required: QuiltAlgorithm implementation
├── requirements.txt     # Optional: Python dependencies
└── README.md            # Optional: documentation
```

### 4.2 quilt.yaml — Package Manifest

```yaml
name: momentum-scalper
type: algorithm                    # "algorithm" or "scraper"
version: 1.0.0
description: Intraday momentum scalping strategy
entry_point: algorithm.py          # Python file containing the QuiltAlgorithm subclass
class_name: MomentumScalper        # Class name within the entry point

requirements:
  asset_types:
    - equities
    - options
  options_level: 3                 # Minimum options approval level (0-4)
  account_features:
    - margin                       # Requires margin account
    - short_selling                # Requires short selling capability
  brokers:                         # Optional — omit to support any broker
    - alpaca
    - tradier
  data_dependencies:
    - name: alpha-picks-scraper
      repo: ElectricJack/alpha-picks-scraper

config:
  parameters:
    - name: risk_per_trade
      type: float
      default: 0.02
      description: Maximum portfolio percentage risked per trade
      min: 0.001
      max: 0.10
    - name: max_positions
      type: int
      default: 5
      description: Maximum concurrent positions
      min: 1
      max: 50
    - name: symbols
      type: list[str]
      default: []
      description: Symbols to trade (empty = algo decides)

notifications:
  custom_events:
    - name: unusual_volume
      description: Triggered when volume exceeds 3x average
      severity: info
    - name: spread_opened
      description: New options spread position opened
      severity: info
    - name: max_drawdown_hit
      description: Portfolio drawdown exceeded threshold
      severity: warning
```

### 4.3 QuiltAlgorithm Base Class

```python
from quilt_trader.sdk import QuiltAlgorithm, TickContext, Signal, SignalType
from typing import Optional

class QuiltAlgorithm:
    """Base class that all trading algorithms must implement."""

    def on_start(self, config: dict, restored_state: Optional[dict]) -> None:
        """Called when the algorithm starts.

        Args:
            config: User-configured parameters from the dashboard.
            restored_state: Output of the last save_state() call if the algorithm
                           was previously running, or None for first run.
                           May be marked stale if manual trading occurred.
        """
        raise NotImplementedError

    def on_tick(self, ctx: TickContext) -> list[Signal]:
        """Called on each tick. Must return a list of signals (can be empty).

        The framework handles order execution, logging, and PDT checking.
        The algorithm should NOT place orders directly.

        Args:
            ctx: Provides access to market data, account positions, data files,
                 and current time.

        Returns:
            List of Signal objects describing desired trades.
        """
        raise NotImplementedError

    def on_stop(self) -> dict:
        """Called on graceful shutdown. Must return state to persist.

        Returns:
            Dictionary of state to restore on next start.
        """
        raise NotImplementedError

    def save_state(self) -> dict:
        """Called by the algorithm whenever it wants to checkpoint state.

        Also called automatically by the framework on SIGTERM/SIGINT.
        The algorithm decides when to call this — framework provides the mechanism.

        Returns:
            Dictionary of state to persist.
        """
        raise NotImplementedError

    def on_signal_rejected(self, signal: Signal, reason: str) -> None:
        """Called when the coordinator rejects a signal (e.g., PDT block).

        Args:
            signal: The rejected signal.
            reason: Human-readable rejection reason.
        """
        pass  # Optional override

    def on_trade_executed(self, signal: Signal, fill: TradeFill) -> None:
        """Called when a trade is successfully executed.

        Args:
            signal: The original signal.
            fill: Execution details including filled price, fees, slippage.
        """
        pass  # Optional override

    def notify(self, event_name: str, message: str, data: Optional[dict] = None) -> None:
        """Send a custom notification event.

        event_name must be declared in quilt.yaml notifications.custom_events.

        Args:
            event_name: Name of the custom event.
            message: Human-readable message.
            data: Optional additional data.
        """
        # Implemented by framework — sends event to coordinator
```

### 4.4 TickContext

```python
class TickContext:
    """Provides all data an algorithm needs during a tick."""

    @property
    def timestamp(self) -> datetime:
        """Current tick timestamp."""

    @property
    def mode(self) -> str:
        """'live' or 'backtest'."""

    @property
    def positions(self) -> dict[str, Position]:
        """Current positions in the account, keyed by symbol."""

    @property
    def account_value(self) -> float:
        """Total account value (cash + positions)."""

    @property
    def cash(self) -> float:
        """Available cash in the account."""

    @property
    def buying_power(self) -> float:
        """Available buying power (accounts for margin)."""

    def market_data(self, symbol: str, timeframe: str = "1min",
                    bars: int = 100) -> pd.DataFrame:
        """Get market data for a symbol.

        Args:
            symbol: Ticker symbol.
            timeframe: Bar timeframe ("1min", "5min", "1hour", "1day").
            bars: Number of historical bars to include.

        Returns:
            DataFrame with columns: open, high, low, close, volume, timestamp.
        """

    def data(self, source_name: str) -> pd.DataFrame:
        """Get data from a custom scraper or data source.

        Args:
            source_name: Name of the data source (matches scraper name in quilt.yaml).

        Returns:
            DataFrame with the scraper's output data.
        """

    def option_chain(self, symbol: str, expiration: Optional[date] = None) -> OptionChain:
        """Get option chain for a symbol.

        Args:
            symbol: Underlying ticker symbol.
            expiration: Specific expiration date, or None for all available.

        Returns:
            OptionChain object with calls and puts.
        """
```

### 4.5 Signal

Signals support both single-leg trades (equities, crypto spot) and multi-leg strategies (options spreads, pairs trades).

```python
class SignalType(Enum):
    BUY = "buy"
    SELL = "sell"
    SELL_SHORT = "sell_short"
    BUY_TO_COVER = "buy_to_cover"

class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"

@dataclass
class SignalLeg:
    """A single leg of a trade signal."""
    symbol: str
    signal_type: SignalType
    quantity: float
    asset_type: str = "equities"       # equities, options, crypto, futures
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None

@dataclass
class Signal:
    """A trade signal produced by an algorithm. Supports single-leg and multi-leg orders."""
    legs: list[SignalLeg]
    strategy_type: str = "single"      # "single", "bull_call_spread", "iron_condor",
                                       # "pairs_trade", "straddle", etc.
    net_debit_limit: Optional[float] = None   # Max net debit for multi-leg orders
    net_credit_limit: Optional[float] = None  # Min net credit for multi-leg orders
    reasoning: Optional[str] = None    # Human-readable explanation of why
    metadata: Optional[dict] = None    # Additional data for logging

    @staticmethod
    def simple(symbol: str, signal_type: SignalType, quantity: float,
               asset_type: str = "equities",
               order_type: OrderType = OrderType.MARKET,
               limit_price: Optional[float] = None,
               reasoning: Optional[str] = None) -> "Signal":
        """Convenience constructor for single-leg signals."""
        return Signal(
            legs=[SignalLeg(symbol=symbol, signal_type=signal_type,
                           quantity=quantity, asset_type=asset_type,
                           order_type=order_type, limit_price=limit_price)],
            strategy_type="single",
            reasoning=reasoning,
        )
```

**Usage examples:**
```python
# Simple equity buy
Signal.simple("AAPL", SignalType.BUY, 100, reasoning="Momentum breakout")

# Crypto spot buy (fractional)
Signal.simple("BTC/USD", SignalType.BUY, 0.05, asset_type="crypto")

# Bull call spread (multi-leg)
Signal(
    legs=[
        SignalLeg("AAPL250620C00200000", SignalType.BUY, 1, asset_type="options"),
        SignalLeg("AAPL250620C00210000", SignalType.SELL, 1, asset_type="options"),
    ],
    strategy_type="bull_call_spread",
    net_debit_limit=3.50,
    reasoning="Bullish into earnings, capping risk",
)

# Crypto pairs trade
Signal(
    legs=[
        SignalLeg("BTC/USD", SignalType.BUY, 0.1, asset_type="crypto"),
        SignalLeg("ETH/USD", SignalType.SELL, 2.0, asset_type="crypto"),
    ],
    strategy_type="pairs_trade",
    reasoning="BTC/ETH ratio reverting to mean",
)
```

### 4.6 Scraper Package Contract

Scrapers follow a similar pattern with a simpler interface:

```yaml
name: alpha-picks-scraper
type: scraper
version: 1.0.0
description: Scrapes alpha stock picks from various sources
schedule: "*/30 * * * *"      # Cron expression: every 30 minutes
output:
  format: csv
  filename: alpha-picks.csv
```

```python
from quilt_trader.sdk import QuiltScraper
import pandas as pd

class QuiltScraper:
    """Base class that all scrapers must implement."""

    def on_start(self, config: dict) -> None:
        """Called once when the scraper is first started."""
        pass

    def on_run(self) -> pd.DataFrame:
        """Called on each scheduled run. Must return output data.

        Returns:
            DataFrame containing the scraped/compiled data.
            Framework handles writing to the configured output format and path.
        """
        raise NotImplementedError

    def on_stop(self) -> None:
        """Called on shutdown."""
        pass
```

---

## 5. Coordinator Services

### 5.1 Algorithm Lifecycle Manager

Handles the full lifecycle of algorithm packages and instances.

**Package Installation:**
1. User selects a repo from the GitHub dropdown in the dashboard (filtered to repos containing `quilt.yaml`)
2. Coordinator clones the repo to a local packages directory
3. Parses and validates `quilt.yaml`
4. Checks that the entry point and class exist and are importable
5. Installs Python dependencies from `requirements.txt` into a virtual environment
6. Records the algorithm in the database with status "installed"
7. On validation failure: records error, shows in dashboard

**Package Updates:**
1. User clicks "Update" in dashboard, or coordinator checks on a schedule
2. Git pull on the local clone
3. Re-validates `quilt.yaml` and entry point
4. If the algorithm has running instances: notifies user, does not auto-restart
5. Updates commit hash and version in database

**Instance Lifecycle:**
1. **Create instance:** User assigns algorithm to account + worker in dashboard, configures parameters
2. **Pre-start validation:**
   - Account compatibility check (asset types, options level, features, broker)
   - Account not locked by another algorithm
   - Required scrapers identified
3. **Start:**
   - Lock the account
   - Start required scrapers (if not already running)
   - Send `start_algorithm` to worker with config, restored state, and broker credentials
   - Worker spawns algorithm subprocess
   - Status → "running"
4. **Running:**
   - Worker streams events, decision logs, and state checkpoints to coordinator
   - Coordinator logs everything, routes events to Discord/dashboard
5. **Stop (graceful):**
   - Send `stop_algorithm` to worker
   - Worker sends SIGTERM to subprocess, algorithm calls `on_stop()` and `save_state()`
   - Worker sends final state to coordinator
   - Coordinator persists state, unlocks account
   - Stop scrapers if no other algorithms depend on them
   - Status → "stopped"
6. **Error:**
   - Worker detects subprocess crash, sends `algo_error`
   - Coordinator logs error, fires Discord alert
   - Account remains locked (user must manually stop/restart)
   - Status → "error"
7. **Disconnected:**
   - Worker misses heartbeats
   - Coordinator marks all algorithms on that worker as "disconnected"
   - Fires Discord alert
   - On worker reconnection: coordinator sends last known state, user decides whether to restart

### 5.2 Event Bus

All system activity flows through the event bus as typed events.

**System events:**
- `algo_started` — Algorithm instance started
- `algo_stopped` — Algorithm instance stopped gracefully
- `algo_error` — Algorithm crashed or raised unhandled exception
- `algo_disconnected` — Worker went offline
- `trade_executed` — Order filled (includes fill details)
- `trade_rejected` — Signal rejected (PDT or other reason)
- `scraper_updated` — Scraper produced new data
- `scraper_error` — Scraper run failed
- `pdt_warning` — Day trade count approaching limit
- `pdt_blocked` — Trade blocked due to PDT protection
- `divergence_detected` — Backtest comparison found significant divergence
- `worker_online` — Worker connected
- `worker_offline` — Worker disconnected
- `data_download_started` — Market data download job started
- `data_download_completed` — Market data download job finished
- `manual_trade_executed` — Manual trade placed from dashboard

**Custom events:**
- Defined per algorithm in `quilt.yaml`
- Sent by algorithms via `self.notify()`
- Coordinator treats them the same as system events for routing

**Event routing:**
- All events are persisted to the Events table
- All events are pushed to the dashboard via WebSocket
- Discord routing is configurable per event type in the dashboard:
  - Each event type can be mapped to a specific Discord channel
  - Events can be enabled/disabled for Discord independently
  - Severity-based filtering (e.g., only send warnings and above)

### 5.3 Worker Manager

Tracks and communicates with worker Pis.

**Registration:**
- Workers are registered in the dashboard with a name and Tailscale IP
- On first connection, the worker agent handshakes with the coordinator and confirms its identity
- Coordinator records the worker as "online"

**Health monitoring:**
- Workers send heartbeats every 30 seconds
- If 3 consecutive heartbeats are missed (90 seconds), the worker is marked "offline"
- Coordinator fires `worker_offline` event
- No automatic migration of algorithms to other workers — user decides

**Reconnection:**
- When a worker reconnects after being offline, coordinator sends it the list of algorithm instances that were running
- Worker does NOT auto-restart algorithms — coordinator notifies the user, who can restart from the dashboard
- This prevents unexpected behavior after an outage

### 5.4 Data Service

Unified API for all data access.

**Market data (Polygon + Theta Data):**
- Supports multiple data providers: Polygon and Theta Data (both supported by Lumibot)
- User kicks off download jobs from the dashboard: select provider, symbols, date range, timeframe
- Downloads run as background tasks with progress tracking
- Data stored as Parquet files on the coordinator filesystem, organized by provider, symbol, and timeframe
- Registry entry created in data_sources table
- API endpoint: `GET /api/data/market/{symbol}?timeframe={tf}&start={date}&end={date}&provider={provider}`
- Provider is optional in the API — defaults to whichever provider has data for the requested symbol/range

**Custom data (scrapers):**
- Scraper output stored on coordinator filesystem
- Registry entry tracks freshness
- API endpoint: `GET /api/data/custom/{scraper_name}`
- Returns the latest output file as a DataFrame-compatible response

**Data freshness:**
- Dashboard shows last-updated timestamps for all data sources
- Algorithms can check data freshness via `TickContext`

### 5.5 Scheduler

Manages recurring tasks on the coordinator.

**Nightly backtest comparisons (configurable time, default 2:00 AM):**
1. For each running algorithm instance:
   - Gather decision logs from the last 24 hours (mode: "live")
   - Replay the same time period using backtest data through the same algorithm
   - Compare signals produced in live vs. backtest
   - Calculate match percentage
   - If divergence exceeds threshold (configurable, default 5%): fire `divergence_detected` event
2. Store results in backtest_comparisons table
3. Available in dashboard with drill-down to individual ticks

**Data archival (configurable schedule, default weekly):**
1. Query decision_log and trade_log for rows older than retention threshold (configurable, default 90 days)
2. Export to Parquet files organized by date range
3. Record in data_archival table
4. Delete archived rows from SQLite
5. Dashboard can load historical data from Parquet files on demand

**Scraper scheduling:**
- Each scraper has a cron expression in its `quilt.yaml`
- Coordinator runs scrapers at their configured intervals
- Only runs scrapers that have at least one dependent algorithm running (or are manually started)

### 5.6 PDT Monitor

Tracks and enforces Pattern Day Trading rules per account.

**Tracking:**
- Monitors all trades (algorithm and manual) per account
- A day trade = opening and closing the same position on the same trading day
- Tracks on a rolling 5-business-day window
- Maintains count in pdt_tracking table
- **Crypto trades are automatically excluded** — PDT rules only apply to equities and options. Trades with `asset_type == "crypto"` are never counted as day trades.
- For multi-leg signals: each leg that could independently constitute a day trade is evaluated. A spread that closes one leg same-day counts as a day trade.

**Enforcement (per account pdt_mode setting):**

| Mode | Behavior |
|------|----------|
| off | No tracking or warnings |
| warn | Tracks day trades, fires `pdt_warning` event at 3 day trades in 5 days. Trade still executes. |
| block | Same as warn, but at the 4th day trade: rejects the signal, fires `pdt_blocked` event, algorithm receives rejection via `on_signal_rejected()` |

**Signal approval flow:**
1. Worker sends `signal_request` to coordinator
2. Coordinator filters out crypto legs (exempt from PDT)
3. Coordinator checks if executing any remaining leg would constitute a day trade
4. If yes and it would be the 4th in 5 days and account is in "block" mode: reject the entire signal (all legs)
5. If yes and it would be the 3rd: approve but fire warning
6. Otherwise: approve
7. Coordinator sends `signal_approved` or `signal_rejected` to worker

### 5.7 GitHub Integration

PAT-based access to the user's GitHub repositories.

**Configuration:**
- User provides a GitHub Personal Access Token in coordinator settings
- Token stored encrypted alongside broker credentials

**Repo discovery:**
- Dashboard queries GitHub API for user's repos
- Filters to repos containing a `quilt.yaml` file (checks file existence via API)
- Presents as a searchable dropdown

**Package management:**
- Install: clone repo, validate manifest, install dependencies
- Update: git pull, re-validate, notify if running instances affected
- Remove: stop running instances, delete local clone, remove from database

---

## 6. Worker Node Architecture

### 6.1 Worker Agent

A single Python process that runs on each worker Pi.

**Startup sequence:**
1. Read local config file (coordinator IP, worker name)
2. Connect to coordinator via WebSocket
3. Send registration message with worker identity and capabilities
4. Enter main loop: listen for commands, send heartbeats

**Process management:**
- Algorithm instances run as separate subprocesses (one per instance)
- Worker agent monitors subprocess health via process polling
- If a subprocess exits unexpectedly: capture exit code and stderr, report `algo_error` to coordinator
- On worker shutdown (SIGTERM): send stop signal to all algorithm subprocesses, wait for graceful shutdown, report final states to coordinator

### 6.2 Algorithm Subprocess

Each algorithm instance runs in isolation.

**Startup:**
1. Worker spawns subprocess with algorithm code path, config, and restored state
2. Subprocess initializes Lumibot broker adapter with provided credentials (in memory only)
3. Calls `algorithm.on_start(config, restored_state)`
4. Enters tick loop

**Tick loop:**
1. Receive tick trigger (market data update from broker via Lumibot)
2. Build `TickContext`:
   - Market data from Lumibot broker adapter
   - Position data from Lumibot broker adapter
   - Custom data fetched from coordinator's data API (cached locally with short TTL)
   - Account info (cash, buying power) from broker
3. Call `algorithm.on_tick(ctx)`
4. Capture returned signals
5. For each signal (single-leg or multi-leg):
   - Send `signal_request` to coordinator (via worker agent), includes all legs
   - Wait for `signal_approved` or `signal_rejected`
   - If approved: execute via Lumibot broker adapter (multi-leg orders submitted as a single composite order where the broker supports it)
   - Report `trade_executed` with fill details for each leg, linked by group_id
   - Coordinator creates/updates Position record
   - If rejected: call `algorithm.on_signal_rejected(signal, reason)`
6. Send `decision_log` entry to coordinator (tick data, signals, reasoning)
7. If algorithm called `save_state()` during the tick: send checkpoint to coordinator

**Crypto / 24/7 market handling:**
- The tick loop does not assume market hours — it runs continuously for crypto assets
- Tick frequency is determined by the broker's data stream (e.g., Alpaca streams crypto 24/7)
- Nightly backtest comparisons for crypto algorithms use a rolling 24-hour window, not "trading day"
- Algorithms can check `ctx.market_data()` to determine if a market is open for equity/options assets

**Data caching:**
- Custom data from coordinator is cached locally with a short TTL (configurable, default 60 seconds)
- Market data comes directly from broker, not cached
- This minimizes Tailscale traffic while keeping data fresh

**Shutdown:**
1. Receive stop signal from worker agent
2. Send SIGTERM to algorithm subprocess
3. Framework calls `algorithm.on_stop()` which returns the final state to persist (this is the canonical final state)
4. Send final state to coordinator via worker agent
5. Subprocess exits

Note: `save_state()` is for mid-run checkpoints. `on_stop()` is the final save on shutdown. The framework calls `save_state()` automatically on SIGTERM/SIGINT as a safety net in case `on_stop()` fails or isn't reached (e.g., during an unclean shutdown).

### 6.3 Order Execution Flow

```
Algorithm.on_tick()
    → returns Signal[]
        → Worker sends signal_request to Coordinator
            → Coordinator checks PDT
            → Coordinator checks account lock
            → signal_approved / signal_rejected
        → If approved: Worker executes via Lumibot broker adapter
            → Broker returns fill
            → Worker sends trade_executed to Coordinator
                → Coordinator logs trade
                → Coordinator updates P/L stats
                → Coordinator routes event to Discord/dashboard
        → If rejected: Worker calls algorithm.on_signal_rejected()
```

### 6.4 Broker Connections

- Broker connections (via Lumibot adapters) run on the worker, not the coordinator
- The worker is the process placing orders — it needs direct broker access
- Credentials are sent from coordinator to worker on algorithm start
- Credentials are held in memory only, never written to worker's filesystem
- If a worker is compromised, credentials aren't persisted on its storage
- Lumibot is used as a library: we use its broker adapter classes for order placement and market data, but NOT its execution loop or strategy lifecycle

**Initially supported brokers (via Lumibot):**
- Alpaca (equities, options, crypto)
- Tradier (equities, options)

### 6.5 Manual Trading Execution

Since broker connections live on workers (not the coordinator), manual trades from the dashboard are routed through a worker:

1. User submits a manual order from the dashboard
2. Coordinator selects a worker to execute it:
   - If the account has a stopped algorithm instance on a worker, use that worker (it can spin up a temporary broker connection)
   - Otherwise, use any online worker (coordinator sends broker credentials for a one-time connection)
3. Worker executes the order via Lumibot broker adapter
4. Worker reports fill details back to coordinator
5. Coordinator logs the trade with source: "manual"

In single-Pi mode (coordinator and worker on same machine), this is effectively local. In multi-Pi mode, the latency of routing through a worker is negligible for manual orders.

---

## 7. Dashboard

### 7.1 Tech Stack

- **Frontend:** React with TypeScript, built with Vite
- **State management:** React Query for server state, Zustand for UI state
- **Charts:** Recharts or Lightweight Charts (TradingView) for financial data
- **Real-time:** WebSocket connection to coordinator for live updates
- **Styling:** Tailwind CSS
- **Served by:** FastAPI static file serving in production

### 7.2 Views

#### 7.2.1 Overview / Home

At-a-glance system status.

- **Worker status cards** — Each worker: name, IP, online/offline, number of running algorithms
- **Algorithm instance summary** — List of all running instances with status, account, current P/L
- **Active alerts** — Recent warnings and errors requiring attention
- **System health** — Coordinator uptime, database size, data freshness indicators
- **Last backtest comparison results** — Quick pass/fail indicators per algorithm

#### 7.2.2 Accounts

Brokerage account management and manual trading.

**Account list view:**
- All configured accounts with broker type, status (locked/available), portfolio value
- Add account button → form: name, broker type, credentials, asset types, options level, features, PDT mode

**Account detail view:**
- Account info and configuration (editable)
- Lock status (which algorithm is running, if any)
- **Portfolio summary:** Total value, cash, buying power, day's P/L
- **Positions table:**
  - For single-leg positions: columns: symbol, quantity, avg cost, current price, market value, unrealized P/L, % change
  - For multi-leg positions (spreads, pairs): expandable row showing strategy type, all legs with individual details, and net position P/L
  - Per-position actions: Close (market/limit, full/partial), set stop-loss
  - For multi-leg: "Close Position" closes all legs simultaneously
  - "Close All Positions" button — flattens everything, all legs of all strategies
- **Manual order entry panel:**
  - Symbol input with autocomplete
  - Side selector: buy, sell, sell short, buy to cover
  - Quantity input
  - Order type: market, limit, stop, stop limit
  - Price inputs (for limit/stop orders)
  - Submit button with confirmation dialog
- **Open orders:** List of pending orders with cancel button
- **Safety guardrails:**
  - If algorithm is running: manual trading controls are disabled
  - "Stop Algorithm & Enable Manual Trading" button → stops algo, marks state as stale, enables controls
  - All manual trades logged with source: "manual"
- **Charts:**
  - Account value over time
  - Fees paid over time (cumulative)
  - Asset allocation pie chart

#### 7.2.3 Algorithms

Algorithm package and instance management.

**Algorithm library view:**
- List of installed algorithm packages with name, version, description, required asset types
- "Install Algorithm" button → GitHub repo browser/dropdown (filtered by quilt.yaml presence)
- Per algorithm: update, remove, view compatibility with each account

**Algorithm detail view:**
- Package info from quilt.yaml
- Compatibility matrix: which accounts meet the requirements (with specific mismatch details)
- List of all instances (current and historical)

**Instance management:**
- Create instance: select account + worker, configure parameters (form generated from config schema)
- Instance detail:
  - Status indicator (running/stopped/error/disconnected)
  - Start/Stop buttons
  - Configuration editor
  - **Performance metrics:**
    - P/L since current run started
    - P/L lifetime (across all runs)
    - Fees current run / lifetime
    - Win rate, average win, average loss
    - Max drawdown
    - Sharpe ratio (if sufficient data)
  - **Current positions** — Live view of what the algorithm is holding
  - **Trade history** — Paginated table of all trades with filters
  - **Equity curve chart** — Account value over time while this algorithm was running
  - **Decision log viewer** — Searchable log of tick-level decisions (paginated, loads from Parquet for historical)

#### 7.2.4 Workers

Worker Pi management.

- List of registered workers with status, IP, running algorithm count
- Add worker: name + Tailscale IP
- Per worker: connection health, resource usage, list of running algorithm instances
- Remove worker (must stop all algorithms first)

#### 7.2.5 Data

Data management hub.

**Market data tab:**
- Download manager: select provider (Polygon/Theta Data), symbols, date range, timeframe, data type → kick off download
- Active downloads with progress bars (percentage, estimated time remaining)
- Browse cached data: table of available symbol/timeframe combinations with date ranges and sizes
- Delete cached data to free space

**Scrapers tab:**
- List of installed scrapers with status, schedule, last run time
- Per scraper: dependent algorithms, run history, output preview
- Manual "Run Now" button
- Error logs

**Data sources overview:**
- Unified table of all data sources (market + custom) with last-updated timestamps
- Data freshness warnings (stale data highlighted)

#### 7.2.6 Backtests

Backtest comparison and divergence analysis.

**Comparison list:**
- Table of all nightly comparison runs with algorithm, date, match percentage
- Color-coded: green (>95% match), yellow (90-95%), red (<90%)
- Trend sparkline showing match percentage over time

**Comparison detail:**
- Summary statistics
- Divergence timeline: chart showing where live and backtest decisions differed
- Side-by-side decision viewer: for each divergent tick, show:
  - Tick timestamp
  - Live tick data vs. backtest tick data (diff highlighted)
  - Live signals vs. backtest signals
  - Algorithm reasoning for each
- Downloadable report (CSV/PDF) for offline analysis or AI agent consumption

#### 7.2.7 Notifications

Discord and event configuration.

- **Channel routing table:** Event type → Discord channel mapping
- Per event type: enable/disable Discord notification, select severity threshold
- Test notification button (sends a test message to the configured channel)
- **Event history:** Paginated, filterable log of all events with Discord delivery status

#### 7.2.8 Settings

System configuration.

- **GitHub:** PAT management (set/update/revoke), connection status
- **Coordinator:** Tailscale IP, data retention days, archival schedule
- **Discord:** Bot token configuration, server/guild selection
- **Data:** Polygon API key, Theta Data credentials, default download settings, preferred provider
- **Backtest:** Nightly comparison time, divergence alert threshold
- **Workers:** Default max algorithms per worker

---

## 8. Discord Bot

### 8.1 Architecture

A full Discord bot (using discord.py) running as part of the coordinator process, not standalone. It shares the same event bus and database access as the rest of the coordinator.

### 8.2 Notifications (Events → Discord)

The bot subscribes to the coordinator's event bus. Based on the routing configuration in the dashboard, it forwards events to the appropriate Discord channels.

**Message formatting:**
- Trade executions: symbol, side, quantity, price, P/L, fees
- Algorithm status changes: algo name, account, old status → new status
- Errors: algo name, error message, stack trace (truncated)
- PDT warnings: account name, day trade count, remaining allowed
- Divergence alerts: algo name, match percentage, top divergences
- Custom algorithm events: formatted per the algorithm's notification definition

### 8.3 Commands (Discord → System)

Future capability — the bot will accept commands for remote management. Initial commands to support:

- `/status` — Overview of all running algorithms and their P/L
- `/positions [account]` — Current positions in an account
- `/stop [instance]` — Stop an algorithm instance
- `/start [instance]` — Start a stopped algorithm instance
- `/alerts` — Recent warnings and errors
- `/pdt [account]` — Current day trade count for an account

Commands require the Discord user ID to match a configured admin user (set in coordinator settings).

---

## 9. Logging and Analysis

### 9.1 Decision Logging

Every tick of every running algorithm is logged with:
- Timestamp
- Mode (live or backtest)
- Complete tick data snapshot (all data the algorithm received)
- Signals produced (or empty list if no action taken)
- Algorithm-provided reasoning/metadata
- Data sources consulted and their freshness timestamps

This creates a complete audit trail that can reconstruct exactly what the algorithm saw and decided at any point in time.

### 9.2 Trade Logging

Every trade (algorithm or manual) is logged with:
- Full order details (symbol, side, quantity, type, prices)
- Fill details (filled price, fees)
- Slippage calculation (filled price vs. requested/signal price)
- P/L (for closing trades)
- Whether it constituted a day trade
- Source (algorithm instance ID or "manual")

### 9.3 Backtest Comparison Engine

**Nightly incremental comparison process:**

1. For each running algorithm instance, retrieve decision logs from the past 24 hours
2. Instantiate the same algorithm class with the same config
3. Replay the same time period using backtest-sourced data:
   - Market data from the Polygon/Theta Data cache (not live broker data)
   - Custom data from scraper output snapshots
4. Run each tick through `on_tick()` and capture signals
5. Compare live signals vs. backtest signals tick-by-tick:
   - Match: same signal type, symbol, and direction
   - Divergence: different signal, or signal present in one but not the other
6. Calculate match percentage and catalog divergences
7. If divergence exceeds the configured threshold: fire `divergence_detected` event
8. Store full results in backtest_comparisons table

**What divergence can indicate:**
- Lookahead bias in backtest data (backtest "knows" future data that live didn't have)
- Data source differences (live broker data vs. Polygon/Theta Data historical data)
- Timing differences (tick timestamps don't align perfectly)
- Bug in algorithm state management (live state drifts from backtest's clean-room state)

### 9.4 Future AI Analysis

The decision log format is designed to be consumable by AI agents:
- JSON-structured data with consistent schema
- Complete context for each decision (what the algo saw, what it decided, why)
- Paired live/backtest data for comparison
- Exportable as Parquet for efficient loading
- An agent (e.g., Claude Code) could be pointed at the exported data to:
  - Identify patterns in divergence
  - Suggest algorithm improvements
  - Detect anomalies in trading behavior
  - Compare performance across parameter configurations

---

## 10. Technology Stack

### 10.1 Coordinator

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| Backend API | FastAPI | Async, WebSocket support, fast, good Python ecosystem |
| Database | SQLite (via aiosqlite) | Simple, no server needed, sufficient for write volume |
| Migrations | Alembic | Standard SQLAlchemy migration tool |
| ORM | SQLAlchemy | Industry standard, works with SQLite and easy migration to PostgreSQL |
| Task scheduling | APScheduler | Lightweight, cron-compatible, in-process |
| Discord bot | discord.py | Most mature Python Discord library |
| GitHub API | PyGithub | Well-maintained GitHub API client |
| Data processing | pandas + pyarrow | DataFrames for data handling, Parquet for archival |
| WebSocket | FastAPI WebSocket (Starlette) | Built into FastAPI |
| Encryption | cryptography (Fernet) | For credential storage at rest |
| Process management | subprocess + asyncio | Algorithm subprocesses on workers |

### 10.2 Worker

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| Worker agent | Python + asyncio | Matches coordinator stack |
| WebSocket client | websockets | Lightweight async WebSocket client |
| Broker adapters | Lumibot (as library) | Broker connection abstraction |
| HTTP client | httpx | Async HTTP for data API requests |

### 10.3 Dashboard

| Component | Technology | Rationale |
|-----------|-----------|-----------|
| Framework | React 18+ with TypeScript | Rich interactivity needed for trading dashboard |
| Build tool | Vite | Fast builds, good dev experience |
| Styling | Tailwind CSS | Utility-first, rapid development |
| State (server) | TanStack Query (React Query) | Caching, auto-refresh, WebSocket integration |
| State (UI) | Zustand | Lightweight, simple |
| Charts | Lightweight Charts (TradingView) | Purpose-built for financial data, candlesticks, equity curves |
| Tables | TanStack Table | Sorting, filtering, pagination |
| Forms | React Hook Form + Zod | Validation for config editors, order entry |
| Icons | Lucide React | Clean, consistent icon set |

### 10.4 Shared SDK

The `quilt-trader-sdk` package (published to PyPI or installed from the main repo) provides:
- `QuiltAlgorithm` base class
- `QuiltScraper` base class
- `TickContext`, `Signal`, `TradeFill`, `Position`, `OptionChain` data classes
- Utility functions for common operations

---

## 11. Directory Structure

```
quilt-trader/
├── docs/
│   └── superpowers/
│       └── specs/
│           └── 2026-05-12-quilt-trader-design.md
├── coordinator/
│   ├── main.py                    # FastAPI app entry point
│   ├── config.py                  # Coordinator configuration
│   ├── database/
│   │   ├── models.py              # SQLAlchemy models
│   │   ├── migrations/            # Alembic migrations
│   │   └── connection.py          # Database setup
│   ├── api/
│   │   ├── routes/
│   │   │   ├── accounts.py        # Account CRUD + manual trading
│   │   │   ├── algorithms.py      # Algorithm package + instance management
│   │   │   ├── workers.py         # Worker registration and status
│   │   │   ├── data.py            # Market data + scraper data API
│   │   │   ├── backtests.py       # Backtest comparison results
│   │   │   ├── events.py          # Event history
│   │   │   └── settings.py        # System settings
│   │   └── websocket.py           # WebSocket handlers (dashboard + workers)
│   ├── services/
│   │   ├── lifecycle.py           # Algorithm lifecycle manager
│   │   ├── event_bus.py           # Event bus
│   │   ├── worker_manager.py      # Worker health + communication
│   │   ├── data_service.py        # Market data + scraper management
│   │   ├── scheduler.py           # APScheduler setup
│   │   ├── pdt_monitor.py         # PDT tracking and enforcement
│   │   ├── github_service.py      # GitHub API integration
│   │   ├── backtest_engine.py     # Nightly backtest comparison
│   │   ├── archival.py            # Data archival to Parquet
│   │   └── discord_bot.py         # Discord bot
│   └── data/
│       ├── market/                # Cached Polygon data (Parquet files)
│       ├── custom/                # Scraper output files
│       ├── packages/              # Cloned algorithm/scraper repos
│       └── archive/               # Archived decision/trade logs (Parquet)
├── worker/
│   ├── main.py                    # Worker agent entry point
│   ├── config.py                  # Worker configuration
│   ├── agent.py                   # WebSocket client + command handler
│   ├── runner.py                  # Algorithm subprocess manager
│   ├── broker_adapter.py          # Lumibot broker wrapper
│   ├── tick_loop.py               # Tick loop + TickContext builder
│   └── data_client.py             # HTTP client for coordinator data API
├── sdk/
│   ├── __init__.py
│   ├── algorithm.py               # QuiltAlgorithm base class
│   ├── scraper.py                 # QuiltScraper base class
│   ├── context.py                 # TickContext
│   ├── signals.py                 # Signal, SignalLeg, SignalType, OrderType
│   └── models.py                  # Position, TradeFill, OptionChain
├── dashboard/
│   ├── package.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── tailwind.config.js
│   ├── src/
│   │   ├── App.tsx
│   │   ├── main.tsx
│   │   ├── api/                   # API client + React Query hooks
│   │   ├── components/            # Shared UI components
│   │   ├── pages/
│   │   │   ├── Overview.tsx
│   │   │   ├── Accounts.tsx
│   │   │   ├── AccountDetail.tsx
│   │   │   ├── Algorithms.tsx
│   │   │   ├── AlgorithmDetail.tsx
│   │   │   ├── InstanceDetail.tsx
│   │   │   ├── Workers.tsx
│   │   │   ├── Data.tsx
│   │   │   ├── Backtests.tsx
│   │   │   ├── BacktestDetail.tsx
│   │   │   ├── Notifications.tsx
│   │   │   └── Settings.tsx
│   │   ├── hooks/                 # Custom hooks (WebSocket, auth, etc.)
│   │   └── types/                 # TypeScript types matching API schema
│   └── public/
├── tests/
│   ├── coordinator/
│   ├── worker/
│   ├── sdk/
│   └── integration/
├── scripts/
│   ├── setup-coordinator.sh       # Coordinator Pi setup
│   ├── setup-worker.sh            # Worker Pi setup
│   └── dev.sh                     # Local development (run coordinator + worker)
├── quilt.schema.json              # JSON schema for quilt.yaml validation
├── pyproject.toml                 # Python project config (coordinator + worker + sdk)
├── README.md
└── .github/
    └── workflows/
        └── ci.yml                 # Linting, type checking, tests
```

---

## 12. Security Considerations

### 12.1 Credential Storage

- Broker API credentials are encrypted at rest in SQLite using Fernet symmetric encryption
- Encryption key is derived from a master password set during coordinator setup, stored in a local file with restricted permissions (0600)
- Credentials are decrypted only when sent to workers or used for manual trading
- Workers hold credentials in memory only — never written to disk
- GitHub PAT and Discord bot token follow the same encryption scheme

### 12.2 Network Security

- All coordinator ↔ worker communication happens over Tailscale (WireGuard encrypted)
- No ports exposed to the public internet
- Dashboard is accessible only over Tailscale or localhost
- WebSocket connections are authenticated with a shared secret configured during worker setup

### 12.3 Algorithm Isolation

- Each algorithm runs in its own subprocess — a crash doesn't affect other algorithms or the worker agent
- Algorithms cannot access other algorithms' state or broker connections
- Algorithms communicate only through the defined SDK interface (TickContext, Signals)
- Algorithms cannot bypass the PDT monitor — all trades go through the coordinator approval flow

---

## 13. Deployment

### 13.1 Coordinator Setup

1. Install Python 3.11+ on the coordinator Pi
2. Clone quilt-trader repo
3. Run `scripts/setup-coordinator.sh`:
   - Creates Python virtual environment
   - Installs dependencies
   - Builds React dashboard
   - Initializes SQLite database with Alembic migrations
   - Prompts for master encryption password
   - Creates systemd service for auto-start
4. Configure via dashboard: GitHub PAT, Discord bot token, Polygon API key, Theta Data credentials

### 13.2 Worker Setup

1. Install Python 3.11+ on the worker Pi
2. Clone quilt-trader repo
3. Run `scripts/setup-worker.sh`:
   - Creates Python virtual environment
   - Installs dependencies
   - Prompts for coordinator Tailscale IP and shared secret
   - Creates systemd service for auto-start
4. Register worker in coordinator dashboard

### 13.3 Single-Pi Mode

Coordinator and worker run on the same Pi. The worker connects to `localhost`. Setup script detects this and configures both services.

---

## 14. Development Workflow

### 14.1 Local Development

- `scripts/dev.sh` starts coordinator and a local worker in development mode
- FastAPI runs with `--reload` for backend changes
- Vite dev server runs with HMR for frontend changes
- SQLite database in a local `dev-data/` directory
- Mock broker adapter available for testing without real brokerage accounts

### 14.2 Algorithm Development

The SDK ships with a `quilt` CLI for local algorithm development on your desktop (faster than developing on a Pi).

**Setup:**
1. `pip install quilt-trader-sdk` (or install from the main repo)
2. Create a new repo with `quilt.yaml` and your `QuiltAlgorithm` implementation

**CLI commands:**
- `quilt dev validate` — Validates `quilt.yaml` schema, checks the entry point and class are importable, verifies the class implements all required methods
- `quilt dev backtest` — Runs your algorithm against historical data and generates a performance report (equity curve, Sharpe, drawdown, trade log). Uses Lumibot's backtesting engine under the hood.
- `quilt dev run` — Runs a live paper-trading session locally, useful for debugging tick-by-tick behavior

**Data modes (configured via `quilt.config.yaml` in the algo repo, git-ignored):**

- **Standalone mode** — Uses local data files or connects directly to Polygon/Theta Data with your own API key. No coordinator needed. Good for getting started or working offline.
- **Connected mode** — Points at your coordinator's data API over Tailscale (configured with coordinator IP). Pulls market data from the coordinator's cache and scraper outputs. This ensures you develop and backtest against the same data your live system uses, which is critical for the live/backtest comparison story.

```yaml
# quilt.config.yaml (git-ignored)
data_mode: connected           # "standalone" or "connected"
coordinator_url: http://100.x.x.x:8000  # Tailscale IP of coordinator
# OR for standalone:
# data_mode: standalone
# polygon_api_key: pk_xxxxx
# theta_data_username: xxxxx
# theta_data_password: xxxxx
```

**Typical development workflow:**
1. Write/modify algorithm code
2. `quilt dev validate` — quick sanity check
3. `quilt dev backtest --start 2025-01-01 --end 2025-06-01` — run backtest, review report
4. Iterate on algorithm logic
5. `quilt dev run` — paper trade for a session to verify live behavior
6. Push to GitHub
7. Install or update from the dashboard

### 14.3 Algorithm Installation & Updates

**Installation (from dashboard):**
1. User clicks "Install Algorithm" in the dashboard
2. Dashboard queries GitHub API for user's repos (filtered by `quilt.yaml` presence)
3. User selects a repo from the dropdown
4. Coordinator clones the repo to `coordinator/data/packages/{algo-name}/`
5. Coordinator creates an isolated virtualenv for the algorithm: `coordinator/data/packages/{algo-name}/.venv/`
6. Installs algorithm's `requirements.txt` into the virtualenv
7. Validates `quilt.yaml` and checks the entry point is importable
8. Records the algorithm in the database with commit hash

**Updates:**
1. User clicks "Update" on an algorithm in the dashboard (or coordinator can check for new commits on a configurable schedule)
2. Coordinator runs `git pull` on the local clone
3. If `requirements.txt` changed: reinstalls dependencies in the virtualenv
4. Re-validates `quilt.yaml` and entry point
5. Updates commit hash and version in the database
6. If the algorithm has running instances: coordinator does NOT auto-restart — it notifies the user via dashboard and Discord that an update is available and a restart is needed to pick it up
7. User decides when to stop and restart instances to pick up the new code

**Deployment to workers:**
1. When an algorithm instance is started, the coordinator sends the algorithm code to the worker
2. Worker creates an isolated virtualenv for the algorithm (if not already present)
3. Worker installs dependencies and starts the subprocess
4. On algorithm update + restart: worker pulls fresh code from coordinator, rebuilds virtualenv if deps changed

**Virtualenv isolation:**
- Each algorithm gets its own virtualenv on both the coordinator (for validation) and workers (for execution)
- This prevents dependency conflicts between algorithms (e.g., one algo needs pandas 1.x, another needs 2.x)
- The SDK itself (`quilt-trader-sdk`) is installed in each virtualenv as a shared dependency

### 14.4 Testing Strategy

- **Unit tests:** SDK classes, coordinator services, worker components
- **Integration tests:** Coordinator ↔ worker communication, algorithm lifecycle, order flow
- **Mock broker:** A Lumibot-compatible mock adapter for testing without real brokers
- **Backtest validation:** Run known algorithms through backtest and verify expected behavior
