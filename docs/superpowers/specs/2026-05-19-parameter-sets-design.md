# Parameter Sets — Design Spec

## Overview

Parameter sets are named, content-addressed collections of algorithm config values that enable a tuning workflow: define parameter permutations, batch-backtest them, compare results, and deploy the winner. Each set has a 6-character hex ID derived from a SHA-256 hash of its config values, making it trivial to verify two deployments have identical configuration.

## Data Model

### New table: `parameter_sets`

| Column | Type | Description |
|---|---|---|
| `id` | String(6), composite PK | First 6 hex chars of SHA-256 of canonical config JSON |
| `algorithm_id` | String, composite PK, FK → algorithms.id | Which algorithm this belongs to |
| `name` | String | User-editable display name (e.g., "BTC Aggressive") |
| `config_values` | JSON | The parameter dict (e.g., `{"symbol": "BTCUSD", "data_source": "thetadata", "fast_window": 5}`) |
| `created_at` | DateTime | When the set was created |
| `updated_at` | DateTime | When the name was last edited |

**Hash computation:**

```python
import hashlib, json
canonical = json.dumps(config_values, sort_keys=True, separators=(",", ":"))
id_hash = hashlib.sha256(canonical.encode()).hexdigest()[:6]
```

**Constraints:**
- Composite unique on `(algorithm_id, id)` — same hash across different algorithms is fine; same hash within one algorithm means identical config and is rejected as a duplicate.
- Cascade delete: deleting an algorithm deletes its parameter sets.
- Values are immutable — editing values creates a new set with a new hash. Only the name is editable in-place.

### Modified existing tables

**`algorithm_instances`** — add column:
- `parameter_set_id`: String(6), nullable — records which set this deployment was created from (composite FK with `algorithm_id` to `parameter_sets`). The actual config is always stored in `config_values` (copy-on-deploy, not a live reference).

**`backtest_runs`** — add column:
- `parameter_set_id`: String(6), nullable — records which set this backtest used (composite FK with `algorithm_id` to `parameter_sets`). The actual config is always stored in `config_overrides`.

## API Endpoints

### Parameter Set CRUD

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/algorithms/{id}/parameter-sets` | Create set. Body: `{name, config_values}`. Computes hash, rejects duplicate. |
| `GET` | `/api/algorithms/{id}/parameter-sets` | List all sets with best backtest metrics per set. |
| `GET` | `/api/algorithms/{id}/parameter-sets/{set_id}` | Get one set by hash ID. |
| `PATCH` | `/api/algorithms/{id}/parameter-sets/{set_id}` | Update name only. |
| `DELETE` | `/api/algorithms/{id}/parameter-sets/{set_id}` | Delete a set. |
| `GET` | `/api/algorithms/{id}/parameter-sets/export` | Export all sets as JSON array (file download). |
| `POST` | `/api/algorithms/{id}/parameter-sets/import` | Import sets from JSON array, skip duplicates by hash. Returns `{imported: N, skipped: M}`. |

### List endpoint response

The list endpoint returns parameter sets enriched with the best backtest metrics (highest Sharpe ratio from completed runs linked to that set):

```json
[
  {
    "id": "e7c4d9",
    "name": "BTC Aggressive",
    "config_values": {"symbol": "BTCUSD", "data_source": "thetadata", "fast_window": 5, "slow_window": 20},
    "created_at": "2026-05-19T14:00:00Z",
    "updated_at": "2026-05-19T14:00:00Z",
    "best_backtest": {
      "sharpe_ratio": 1.82,
      "total_return_pct": 34.2,
      "max_drawdown_pct": -12.1,
      "run_count": 3
    }
  }
]
```

Sets with no backtests have `best_backtest: null`.

### Modified existing endpoints

**`POST /api/algorithms/{id}/instances`** — add optional `parameter_set_id` field. If provided, copies `config_values` from that set and records the set ID for traceability.

**`POST /api/backtest-runs`** — add optional `parameter_set_id` field. If provided, copies config from the set into `config_overrides` and records the set ID.

## Export/Import Format

```json
[
  {
    "name": "BTC Aggressive",
    "config_values": {"symbol": "BTCUSD", "data_source": "thetadata", "fast_window": 5, "slow_window": 20}
  },
  {
    "name": "SPY Default",
    "config_values": {"symbol": "SPY", "data_source": "polygon", "fast_window": 10, "slow_window": 30}
  }
]
```

- IDs are omitted from export — recomputed from values on import.
- Import skips any set whose computed hash already exists for that algorithm.
- Export triggers a file download. Import accepts a `.json` file upload.

## UI Changes

### Algorithm Detail Page — Parameter Sets Section

Positioned between the Details card and the Deployments section. Contains:

**Header row:**
- Section title: "Parameter Sets"
- Action buttons: Import, Export, + New Set, Backtest All

**Table columns:**
- **ID** — 6-char hex hash, monospace, styled as a badge
- **Name** — user-editable display name
- **Parameters** — compact inline preview of config values (e.g., `BTCUSD / 5 / 20`)
- **Sharpe** — from best backtest run (or `--` if none)
- **Return** — total return % from best backtest
- **Max DD** — max drawdown % from best backtest
- **Runs** — count of backtest runs for this set
- **Actions** — Backtest, Deploy buttons per row

**Visual treatment:**
- Table sorted by Sharpe ratio descending by default
- Best performer row highlighted with a subtle green tint
- Metrics colored: green for positive returns/Sharpe, red for poor drawdown, `--` in gray for untested sets

**"+ New Set" button:** Opens a modal with:
- Name field (text input)
- Config fields generated from the algorithm's `config_schema` (one field per parameter, typed appropriately)
- Save button that POSTs to the create endpoint

### Deploy Modal Changes

- Add a "Load from parameter set" dropdown at the top, listing sets by name and hash ID (e.g., "BTC Aggressive (e7c4d9)")
- Selecting a set populates the config fields with its values
- User can still edit values after loading — the deployment snapshots whatever is in the fields
- The `parameter_set_id` is sent along for traceability

### Backtest Modal Changes

- Add a "Load from parameter set" dropdown (same pattern as deploy)
- Selecting a set populates `config_overrides`
- The per-row "Backtest" button on the parameter sets table opens the backtest modal pre-loaded with that set's config
- The `parameter_set_id` is sent along

### "Backtest All" Flow

- Opens a simplified modal asking only for date range and initial cash
- Creates one backtest run per parameter set using those shared settings and each set's config values
- Uses the existing `POST /api/backtest-runs` endpoint sequentially from the frontend
- A future optimization could add a batch API endpoint

## Data Source as Config Parameter

Data sources (Polygon, ThetaData, etc.) are treated as regular algorithm config parameters, not special infrastructure. Algorithms that need to specify a data source include it in their `quilt.yaml` manifest:

```yaml
config:
  parameters:
    - name: data_source
      type: string
      default: polygon
      description: Market data provider
```

This means data source configuration is naturally part of parameter sets. A set like "BTC Aggressive" includes `"data_source": "thetadata"` alongside `"symbol": "BTCUSD"`. Each parameter set is fully self-contained: everything needed to run the algorithm in a specific way.

Backtest-specific settings (date range, initial cash) are NOT part of parameter sets — they are per-run concerns configured at backtest time.

## Copy-on-Deploy Semantics

When deploying or backtesting with a parameter set:
1. The `config_values` are copied from the set into the deployment/backtest record
2. The `parameter_set_id` is recorded for traceability
3. The deployment/backtest is independent — editing or deleting the parameter set does not affect running deployments or completed backtests
4. This ensures running deployments are predictable and reproducible

## Migration

Single Alembic migration that:
1. Creates the `parameter_sets` table
2. Adds `parameter_set_id` column (nullable) to `algorithm_instances`
3. Adds `parameter_set_id` column (nullable) to `backtest_runs`
