# QuiltTrader

A distributed algorithmic trading framework. Run the coordinator on your PC, deploy algorithms to Raspberry Pi workers, and monitor everything from a real-time dashboard.

## Architecture

```
┌─────────────────────────────────────────────────┐
│                 Your PC / Server                 │
│                                                  │
│  ┌────────────┐  ┌───────────┐  ┌────────────┐  │
│  │Coordinator │  │ Dashboard │  │  Market     │  │
│  │  (FastAPI) │──│  (React)  │  │  Data Store │  │
│  │  port 8000 │  │  served   │  │  (Parquet)  │  │
│  └─────┬──────┘  │  at /     │  └────────────┘  │
│        │         └───────────┘                   │
└────────┼─────────────────────────────────────────┘
         │ Tailscale VPN (WebSocket)
    ┌────┴─────┐    ┌──────────┐    ┌──────────┐
    │ Worker 1 │    │ Worker 2 │    │ Worker N │
    │ (Raspi)  │    │ (Raspi)  │    │  (any)   │
    │ algo-a   │    │ algo-b   │    │ algo-c   │
    └──────────┘    └──────────┘    └──────────┘
```

**Coordinator** — the central control plane running on your PC, laptop, or a server. Manages algorithms, broker accounts, market data, and the dashboard. Doesn't execute algorithms itself — it orchestrates.

**Workers** — lightweight agents running on Raspberry Pis (or any Linux machine). Each worker connects to the coordinator over Tailscale, receives algorithm deployments, executes trades, and reports back. Workers are stateless — the coordinator holds all configuration and can redeploy to any worker at any time.

**Dashboard** — a React web app served by the coordinator. Real-time portfolio monitoring, equity curves, algorithm deployment management, and data browsing.

**Tailscale** — the mesh VPN that connects everything. Workers join your Tailnet during installation and communicate with the coordinator over encrypted WebSocket connections. No port forwarding or public IPs required.

## Prerequisites

- **Python 3.11+** on the coordinator machine
- **Node.js 18+** for building the dashboard (one-time)
- **A Tailscale account** (free tier is fine) for connecting workers
- **A broker account** — Alpaca or Tradier (paper or live)

## Quick Start

### 1. Install the coordinator

```bash
git clone https://github.com/ElectricJack/quilt-trader.git
cd quilt-trader

# Install with coordinator dependencies
pip install -e ".[coordinator,dev]"

# Initialize config and database
quilt init

# Build the dashboard
cd dashboard && npm install && npm run build && cd ..

# Start the coordinator
quilt up
```

The coordinator is now running at `http://localhost:8000`. The dashboard is served at the root URL.

### 2. Add a broker account

Open the dashboard at `http://localhost:8000` and navigate to **Accounts → Add Account**. You'll need:

- **Alpaca**: API Key + Secret Key (get from https://app.alpaca.markets)
- **Tradier**: Access Token + Account ID (get from https://developer.tradier.com)

You can add paper accounts for testing before connecting live accounts.

### 3. Set up Tailscale

Install Tailscale on your coordinator machine if you haven't already:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up
```

Generate a reusable auth key at https://login.tailscale.com/admin/settings/keys — you'll need this for worker installation.

> **WSL2 users**: The coordinator running inside WSL2 isn't directly reachable from Tailscale. See [WSL2 + Tailscale Setup](docs/notes/wsl-tailscale-setup.md) for the fix (one-time port forwarding or mirrored networking).

### 4. Add a worker

From the coordinator machine:

```bash
quilt worker add --name pi-1
```

This prints a one-liner install command. SSH into your Raspberry Pi and paste it:

```bash
curl -fsSL '<install-url>' | \
  TAILSCALE_AUTHKEY='tskey-...' \
  COORDINATOR_URL='http://<your-tailscale-ip>:8000' \
  WORKER_ID='...' WORKER_NAME='pi-1' WORKER_TOKEN='...' \
  sudo -E bash
```

The script installs Tailscale, joins your network, downloads the worker package, and starts a systemd service. The worker appears in the dashboard within seconds.

### 5. Install and deploy an algorithm

```bash
# Install from a local directory or GitHub URL
quilt algorithm install ./my-algo --as my-algo-v1

# Create a deployment (links algorithm + account + worker)
quilt deployment create --algo my-algo-v1 --account "Alpaca Paper" --worker pi-1

# Start trading
quilt deployment start <deployment-id>

# Watch live activity
quilt deployment activity <deployment-id> --follow
```

## Writing Algorithms

Algorithms are Python packages with a `quilt.yaml` manifest and a class extending `QuiltAlgorithm`:

```yaml
# quilt.yaml
name: my-strategy
type: algorithm
version: 1.0.0
entry_point: algorithm.py
class_name: MyStrategy
trigger: interval:60s

requirements:
  asset_types:
    - equities
```

```python
# algorithm.py
from sdk.algorithm import QuiltAlgorithm
from sdk.signals import Signal, SignalLeg, SignalType, OrderType

class MyStrategy(QuiltAlgorithm):
    def on_start(self, config, restored_state):
        self.threshold = config.get("threshold", 0.02)

    def on_tick(self, ctx):
        # ctx.positions — current holdings
        # ctx.cash, ctx.account_value — account state
        # ctx.market_data(symbol, timeframe, bars) — price data
        # ctx.data(source_name) — custom data (scraper output, CSVs)

        signals = []
        # ... your logic here ...
        return signals

    def on_stop(self):
        return {"last_run": "..."}  # persisted state
```

**Trigger types:**
- `interval:60s` — run every 60 seconds during market hours
- `bar:1min` — run on each 1-minute bar for subscribed symbols
- `event` — run on each trade/quote event

## Market Data

QuiltTrader manages market data from multiple sources:

```bash
# Subscribe to live streaming data
quilt data subscribe alpaca AAPL --retention-hours 720

# Download historical bars
quilt data download --symbol AAPL --start 2024-01-01 --end 2024-12-31

# Check installed scrapers
quilt data scrapers
```

**Data providers**: Polygon (2-year free history), Tradier (10+ year history with brokerage account), Alpaca (requires paid data plan).

Data is stored as Parquet files in `data/market/{provider}/{symbol}/{timeframe}.parquet` and is browsable from the dashboard's Data tab.

## CLI Reference

```
quilt init                              # Initialize config + database
quilt up / quilt down                   # Start/stop the coordinator
quilt coord status / logs               # Check coordinator health

quilt account list                      # List broker accounts
quilt algorithm list / install / show   # Manage algorithms
quilt worker list / add / update        # Manage workers
quilt deployment list / create / start  # Manage deployments
quilt deployment activity <id> --follow # Stream live logs

quilt data subscribe <broker> <symbol>  # Start live data stream
quilt data download --symbol <sym>      # Download historical data
quilt data scrapers                     # Scraper status

quilt backtest run --algo <name> --start <date> --end <date> --wait
quilt doctor                            # Diagnose common issues
```

**Global flags:** `--json` (machine output), `--coord <url>` (override coordinator), `-q` (quiet)

**Exit codes:** `0` success, `1` internal error, `2` user error, `3` coordinator unreachable, `4` operation failed

## Updating Workers

Workers installed via the one-liner don't have git — they receive code updates as tarballs from the coordinator:

```bash
quilt worker update pi-1
```

This downloads a fresh worker package from the coordinator, reinstalls dependencies, and restarts the systemd service. If a worker does have a git clone, it will use `git pull` instead.

## Project Structure

```
quilt-trader/
├── sdk/              # Shared SDK — QuiltAlgorithm base class, signals, CLI
│   ├── algorithm.py  # Base class for algorithms
│   ├── signals.py    # Signal/SignalLeg/OrderType definitions
│   ├── context.py    # TickContext interface
│   └── cli/          # `quilt` CLI commands
├── coordinator/      # Central server (FastAPI)
│   ├── main.py       # App factory, service wiring
│   ├── api/          # REST + WebSocket routes
│   ├── services/     # Business logic (scheduler, data, lifecycle)
│   └── database/     # SQLAlchemy models + Alembic migrations
├── worker/           # Worker agent (runs on Pis)
│   ├── main.py       # Entry point
│   ├── agent.py      # WebSocket message handler
│   ├── tick_loop.py  # Algorithm execution loop
│   └── *_adapter.py  # Broker adapters (Alpaca, Tradier)
├── dashboard/        # React frontend
│   └── src/
├── data/             # Runtime data (gitignored)
│   ├── market/       # Parquet price files
│   ├── custom/       # Scraper output
│   └── packages/     # Installed algorithms + scrapers
├── scripts/          # Install scripts, utilities
└── tests/            # Test suite
```

## Shell Completion

```bash
# bash
_QUILT_COMPLETE=bash_source quilt >> ~/.bashrc

# zsh
_QUILT_COMPLETE=zsh_source quilt >> ~/.zshrc

# fish
_QUILT_COMPLETE=fish_source quilt > ~/.config/fish/completions/quilt.fish
```

## License

Private — not yet open source.
