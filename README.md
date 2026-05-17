# QuiltTrader

Algorithmic trading framework designed to run on one or more Raspberry Pi nodes.

## Development notes

- Running the coordinator in WSL2 with worker Pis on Tailscale: see
  [docs/notes/wsl-tailscale-setup.md](docs/notes/wsl-tailscale-setup.md).

## CLI

QuiltTrader ships a `quilt` CLI for all operator and author workflows. Run `quilt --help` for the full command tree.

### Setup

```sh
quilt init          # writes ~/.quilt/config.yaml and runs migrations
quilt up            # starts the coordinator (which serves the dashboard at /)
quilt doctor        # diagnoses common breakage
```

### Workflows

**Install and backtest an algorithm:**
```sh
quilt algorithm install ./my-algo --as my-algo-dev
quilt backtest run --algo my-algo-dev \
  --start 2024-01-01 --end 2024-12-31 --wait
```

**Add a worker and deploy:**
```sh
quilt worker add --name pi-1            # prints install one-liner
# (paste the one-liner on the Pi to install the worker)
quilt deployment create --algo my-algo-dev --account alpaca-paper --worker pi-1
quilt deployment start <deployment-id>
quilt deployment activity <deployment-id> --follow
```

**Data control:**
```sh
quilt data subscribe alpaca AAPL
quilt data download --symbol AAPL --start 2024-01-01 --end 2024-12-31
quilt data scrapers
```

### Global flags

- `--json` — machine-readable output (works on every command that returns data)
- `--coord <url>` — override coordinator URL (default: from `~/.quilt/config.yaml`)
- `-q` / `--quiet` — suppress non-essential output

### Exit codes

- `0` — success
- `1` — internal/unexpected error
- `2` — user error (bad args, not found, refusing destructive op without `--yes`)
- `3` — coordinator unreachable
- `4` — operation failed (server rejected the request)

### Shell completion

```sh
# bash:
_QUILT_COMPLETE=bash_source quilt >> ~/.bashrc

# zsh:
_QUILT_COMPLETE=zsh_source quilt >> ~/.zshrc

# fish:
_QUILT_COMPLETE=fish_source quilt > ~/.config/fish/completions/quilt.fish
```
