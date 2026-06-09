# Getting Started

By the end of this guide you will have:

- The coordinator running on `http://localhost:8000`.
- A paper broker account (Alpaca) connected to your install.
- A localhost worker registered and online.
- One toy algorithm installed, backtested against historical data, and (optionally) deployed live against the paper broker.

Target time: 30 minutes.

## Before you start

Prerequisites:

- Python 3.11 or newer. On Ubuntu / WSL the binary is usually called `python3` (not `python`). Use `python3` and `pip3` everywhere in this guide if `python` is not on your `PATH`.
- Node 18 or newer (for the dashboard build).
- A free Alpaca paper-trading account. Sign up at <https://app.alpaca.markets> and grab an API key + secret from the paper dashboard.

Optional:

- A Tailscale account — only required if you later want to add a **remote** worker (e.g. a Raspberry Pi). Not needed for a localhost worker.
- A second Linux machine if you want to follow the distributed-execution doc later.

## Step 1 — Install the coordinator

```bash
git clone https://github.com/your-org/quilt-trader.git
cd quilt-trader
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[coordinator,dev]"
```

Initialize the local config and run the database migrations:

```bash
quilt init
```

Build the dashboard (one-time):

```bash
cd dashboard && npm install && npm run build && cd ..
```

Start the coordinator:

```bash
quilt up
```

`quilt up` is shorthand for `quilt coord start`. The coordinator daemonizes by default. Open the dashboard at <http://localhost:8000>.

To stop the coordinator later:

```bash
quilt down
```

## Step 2 — Connect a paper broker account

In the dashboard:

1. Go to **Accounts → Add account**.
2. Pick **Alpaca Paper**.
3. Paste your paper API key and secret from <https://app.alpaca.markets>.
4. Name the account `Alpaca Paper`.
5. Click **Test connection**, then **Save**.

The account should now appear on the **Accounts** page with status `connected`.

## Step 3 — Add a localhost worker

For a production deployment to a Raspberry Pi or remote host you would use the install one-liner (which assumes Tailscale); see [`distributed-execution.md`](../concepts/distributed-execution.md). For a local-only dev loop you can run the worker directly from this repo, without Tailscale.

Register the worker with the coordinator:

```bash
quilt worker add --name local-dev
```

The CLI prints two values you need to capture:

```
created worker: a1b2c3d4 (local-dev)
install_token: <long random string>
```

Run the worker process from the repo root. The worker needs the websockets extras:

```bash
pip install -e ".[worker]"

export WORKER_TOKEN='<install_token from above>'
export QTW_WORKER_ID='<full worker id — use `quilt worker show local-dev` to see it>'
export QTW_WORKER_NAME='local-dev'
export QTW_COORDINATOR_URL='ws://localhost:8000'

python3 -m worker.main
```

Leave that terminal open. You should see the worker connect; in the dashboard's **Workers** tab the worker now shows `online`.

### Clearing the `pending` install status (cosmetic)

The dashboard will show `install_status: pending` for this worker because we skipped the install script. Everything still works — this only affects the badge. To clear it, in another terminal:

```bash
curl -X POST \
  "http://localhost:8000/api/workers/install/claim/${QTW_WORKER_ID}?token=${WORKER_TOKEN}"
```

## Step 4 — Install the toy algorithm

> **Toy example — do not trade real money against this.** It exists only to give you something that compiles and runs end-to-end.

Create a directory and drop two files into it.

```bash
mkdir -p ./toy-momentum
```

`./toy-momentum/algorithm.py`:

```python
from __future__ import annotations

from typing import TYPE_CHECKING

from sdk.algorithm import QuiltAlgorithm
from sdk.signals import Signal, SignalType, OrderType

if TYPE_CHECKING:
    from sdk.context import TickContext


class ToyMomentum(QuiltAlgorithm):
    """Buy 1 share on bullish EMA cross, sell 1 share on bearish EMA cross."""

    def on_start(self, config: dict, restored_state: dict | None) -> None:
        self.symbol = config.get("symbol", "SPY")
        self.threshold = float(config.get("threshold", 0.02))
        self.last_state = (restored_state or {}).get("last_state", "flat")

    def on_tick(self, ctx: "TickContext") -> list[Signal]:
        bars = ctx.market_data(self.symbol, "1min", bars=20)
        if bars is None or len(bars) < 12:
            return []
        close = bars["close"]
        short = close.ewm(span=5, adjust=False).mean().iloc[-1]
        long_ = close.ewm(span=12, adjust=False).mean().iloc[-1]
        spread = (short - long_) / long_

        if spread > self.threshold and self.last_state != "long":
            self.last_state = "long"
            return [Signal.simple(self.symbol, SignalType.BUY, 1,
                                  asset_type="equities", order_type=OrderType.MARKET,
                                  reasoning=f"EMA spread {spread:.3%} > {self.threshold:.2%}")]
        if spread < -self.threshold and self.last_state != "short":
            self.last_state = "short"
            return [Signal.simple(self.symbol, SignalType.SELL, 1,
                                  asset_type="equities", order_type=OrderType.MARKET,
                                  reasoning=f"EMA spread {spread:.3%} < -{self.threshold:.2%}")]
        return []

    def save_state(self) -> dict:
        return {"last_state": self.last_state}

    def on_stop(self) -> dict:
        return self.save_state()
```

`./toy-momentum/quilt.yaml`:

```yaml
name: toy-momentum
type: algorithm
version: 0.1.0
description: A tiny EMA-crossover toy algorithm for the getting-started guide.
entry_point: algorithm.py
class_name: ToyMomentum
trigger: interval:60s
requirements:
  asset_types:
    - equities
config:
  parameters:
    - name: symbol
      type: string
      default: SPY
    - name: threshold
      type: number
      default: 0.02
assets:
  - symbol: SPY
    asset_class: equities
    timeframe: 1min
    source: polygon
```

Install it:

```bash
quilt algorithm install ./toy-momentum --as toy-momentum-v1
```

Confirm:

```bash
quilt algorithm list
```

## Step 5 — Run a backtest

The backtest path reads bars from the local parquet store, so the algorithm's `bars is None` early-return doesn't bite as long as we download the slice first.

Download a month of SPY history:

```bash
quilt data download --symbol SPY --start 2024-06-01 --end 2024-06-30
```

Then run the backtest and wait for it to finish:

```bash
quilt backtest run --algo toy-momentum-v1 \
  --start 2024-06-01 --end 2024-06-30 --wait
```

Open the dashboard's **Backtests** tab to see the equity curve, trades, and stats. The toy algorithm will not look impressive — that's expected. You have proven the pipeline end-to-end.

## Step 6 — Deploy live (optional)

> **Heads up.** This step requires a live 1-minute SPY data subscription. Without it, `ctx.market_data(...)` returns `None` on every tick and the algorithm will just sit there idle. Either subscribe first (below) or skip this step.

Subscribe to live SPY 1-minute bars via your paper account:

```bash
quilt data subscribe alpaca SPY
```

Create and start a deployment:

```bash
quilt deployment create \
  --algo toy-momentum-v1 \
  --account "Alpaca Paper" \
  --worker local-dev

quilt deployment start <deployment-id>
quilt deployment activity <deployment-id> --follow
```

`activity --follow` streams ticks, signals, and orders. To stop:

```bash
quilt deployment stop <deployment-id>
```

## What to read next

Pick by intent:

- **Big picture of how the system fits together** → [`concepts/architecture.md`](../concepts/architecture.md)
- **Write your own algorithm** → [`concepts/writing-algorithms.md`](../concepts/writing-algorithms.md)
- **Make backtests honest** (slippage, fees, MTM) → [`concepts/backtest-accuracy.md`](../concepts/backtest-accuracy.md)
- **Wire in market data sources** → [`concepts/data-collection.md`](../concepts/data-collection.md)
- **Deploy across a fleet of workers** → [`concepts/distributed-execution.md`](../concepts/distributed-execution.md)
- **Build custom scrapers / alt-data feeds** → [`concepts/scrapers.md`](../concepts/scrapers.md)
- **Drive Quilt from an AI agent / Claude** → [`concepts/cli-and-agentic-workflows.md`](../concepts/cli-and-agentic-workflows.md)

## Troubleshooting first-run failures

| Symptom | Likely cause | Fix |
|---|---|---|
| `quilt up` exits with `port 8000 already in use` | Another service is bound to 8000 | Kill the offender, or run `quilt coord start --port 8001` and visit that port |
| `python: command not found` | Ubuntu / WSL only ships `python3` | Use `python3` and `pip3` everywhere; or alias `python=python3` |
| Worker stays `pending` in the dashboard | Localhost worker never hit the install-claim endpoint | Run the `curl ... /api/workers/install/claim/...` command from Step 3 |
| `on_tick` logs show `bars is None` | No SPY data is available on disk (backtest) or no live subscription (deploy) | Run `quilt data download --symbol SPY --start ... --end ...` for backtests, or `quilt data subscribe alpaca SPY` for live |
| `npm run build` fails with a syntax error | Node version is older than 18 | Upgrade Node (`nvm install 20` is the easy path) |
| `pip install -e` fails with "editable install requires setuptools" | Old pip | `pip install -U pip` then retry |
| Worker process exits with `WORKER_TOKEN not set` | Forgot to `export` the env vars before `python -m worker.main` | Re-export `WORKER_TOKEN`, `QTW_WORKER_ID`, `QTW_WORKER_NAME`, `QTW_COORDINATOR_URL` in the same shell |
