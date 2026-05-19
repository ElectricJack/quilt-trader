# Parameter Sets — Implementation Plan

**Spec**: `docs/superpowers/specs/2026-05-19-parameter-sets-design.md`

## Phase 1: Database Model & Migration

### Step 1.1: Add ParameterSet model to `coordinator/database/models.py`

Add after the `Algorithm` model (~line 78):

```python
class ParameterSet(Base):
    __tablename__ = "parameter_sets"
    __table_args__ = (
        sa.PrimaryKeyConstraint("algorithm_id", "id"),
    )

    id: Mapped[str] = mapped_column(String(6), nullable=False)
    algorithm_id: Mapped[str] = mapped_column(
        String, ForeignKey("algorithms.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    config_values: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    algorithm: Mapped["Algorithm"] = relationship(back_populates="parameter_sets")
```

Add to Algorithm model:
```python
parameter_sets: Mapped[list["ParameterSet"]] = relationship(
    back_populates="algorithm", cascade="all, delete-orphan"
)
```

Add `parameter_set_id` column (String(6), nullable) to `AlgorithmInstance` and `BacktestRun` models. No FK constraint needed — the set may be deleted while deployments/backtests remain.

Add a helper function:
```python
def compute_parameter_set_id(config_values: dict) -> str:
    import hashlib, json
    canonical = json.dumps(config_values, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:6]
```

### Step 1.2: Create Alembic migration

Run `alembic revision --autogenerate -m "parameter_sets"` and verify the generated migration:
1. Creates `parameter_sets` table with composite PK
2. Adds `parameter_set_id` column to `algorithm_instances`
3. Adds `parameter_set_id` column to `backtest_runs`

Run `alembic upgrade head` to apply.

---

## Phase 2: API Endpoints

### Step 2.1: Create `coordinator/api/routes/parameter_sets.py`

New route file with the following endpoints. Follow the pattern in `algorithms.py` (Pydantic models, `Depends(get_db)`, HTTPException for 404s).

**Pydantic models:**

```python
class ParameterSetCreate(BaseModel):
    name: str
    config_values: dict

class ParameterSetUpdate(BaseModel):
    name: str

class ParameterSetImport(BaseModel):
    sets: list[ParameterSetCreate]
```

**Endpoints:**

- `POST /api/algorithms/{algorithm_id}/parameter-sets`
  - Validate algorithm exists
  - Compute hash from `config_values`
  - Check for duplicate hash within this algorithm — return 409 if exists
  - Create and return the new ParameterSet

- `GET /api/algorithms/{algorithm_id}/parameter-sets`
  - Query all parameter sets for the algorithm
  - For each set, join to `backtest_runs` where `parameter_set_id` matches and status is completed
  - Find the best backtest per set (highest `sharpe_ratio`)
  - Return list enriched with `best_backtest` object (sharpe_ratio, total_return, max_drawdown, run_count) or null
  - Sort by sharpe_ratio descending (nulls last)

- `GET /api/algorithms/{algorithm_id}/parameter-sets/{set_id}`
  - Lookup by composite key (algorithm_id, set_id)
  - Return 404 if not found

- `PATCH /api/algorithms/{algorithm_id}/parameter-sets/{set_id}`
  - Update name only
  - Set `updated_at` to now

- `DELETE /api/algorithms/{algorithm_id}/parameter-sets/{set_id}`
  - Delete by composite key
  - Nullify `parameter_set_id` on any referencing instances/backtests (or leave as-is since no FK constraint)

- `GET /api/algorithms/{algorithm_id}/parameter-sets/export`
  - Return JSON array of `{name, config_values}` for all sets
  - Set `Content-Disposition: attachment; filename="parameter-sets.json"`

- `POST /api/algorithms/{algorithm_id}/parameter-sets/import`
  - Accept JSON array of `{name, config_values}`
  - Compute hash for each, skip if hash already exists for this algorithm
  - Return `{imported: N, skipped: M}`

### Step 2.2: Register routes in `coordinator/api/app.py`

Add the new router. Check how existing routers are included (likely `app.include_router(router)`).

### Step 2.3: Modify existing endpoints

**In `coordinator/api/routes/algorithms.py`:**
- `InstanceCreate` model: add `parameter_set_id: Optional[str] = None`
- `create_instance` handler: if `parameter_set_id` is provided, look up the parameter set, copy its `config_values` to the instance, and store the `parameter_set_id`

**In `coordinator/api/routes/backtest_runs.py`:**
- `BacktestRunCreate` model: add `parameter_set_id: Optional[str] = None`
- Create handler: if `parameter_set_id` is provided, look up the parameter set, copy its `config_values` into `config_overrides`, and store the `parameter_set_id`

---

## Phase 3: Frontend API Layer

### Step 3.1: Add TypeScript types in `dashboard/src/types/index.ts`

```typescript
export interface ParameterSet {
  id: string;
  algorithm_id: string;
  name: string;
  config_values: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  best_backtest: {
    sharpe_ratio: number | null;
    total_return: number | null;
    max_drawdown: number | null;
    run_count: number;
  } | null;
}
```

### Step 3.2: Add API client methods in `dashboard/src/api/client.ts`

Follow the existing pattern (`request<T>()` helper):

- `listParameterSets(algorithmId: string): Promise<ParameterSet[]>`
- `createParameterSet(algorithmId: string, body: {name: string, config_values: Record<string, unknown>}): Promise<ParameterSet>`
- `updateParameterSet(algorithmId: string, setId: string, body: {name: string}): Promise<ParameterSet>`
- `deleteParameterSet(algorithmId: string, setId: string): Promise<void>`
- `exportParameterSets(algorithmId: string): Promise<Blob>` (fetch with blob response)
- `importParameterSets(algorithmId: string, sets: Array<{name: string, config_values: Record<string, unknown>}>): Promise<{imported: number, skipped: number}>`

### Step 3.3: Add React Query hooks in `dashboard/src/api/hooks.ts`

Follow the existing mutation/query patterns:

- `useParameterSets(algorithmId: string)` — query hook, key: `["algorithms", id, "parameter-sets"]`
- `useCreateParameterSet(algorithmId: string)` — mutation, invalidates parameter-sets key
- `useUpdateParameterSet(algorithmId: string)` — mutation, invalidates parameter-sets key
- `useDeleteParameterSet(algorithmId: string)` — mutation, invalidates parameter-sets key
- `useImportParameterSets(algorithmId: string)` — mutation, invalidates parameter-sets key

---

## Phase 4: Parameter Sets UI Section

### Step 4.1: Create `dashboard/src/components/ParameterSetsSection.tsx`

New component rendered on the AlgorithmDetail page between Details and Deployments.

**Props:**
```typescript
interface Props {
  algorithmId: string;
  manifestConfig: Array<{ name: string; type: string; default?: unknown }>;
}
```

**Contains:**
- Header row with title and action buttons (Import, Export, + New Set, Backtest All)
- DataTable with columns: ID, Name, Parameters (compact preview), Sharpe, Return, Max DD, Runs, Actions
- Best performer row highlighted (highest Sharpe) with subtle green background
- Table sorted by Sharpe descending, nulls last
- Per-row actions: Backtest button, Deploy button
- Empty state: "No parameter sets defined. Create one to start tuning."

**Metrics formatting:**
- Sharpe: 2 decimal places, green if > 0
- Return: percentage with 1 decimal, green/red by sign
- Max DD: percentage with 1 decimal, red/yellow by severity
- Runs: integer count, gray
- `--` in gray for sets with no backtests

### Step 4.2: Create `dashboard/src/components/CreateParameterSetModal.tsx`

Modal for creating a new parameter set.

**Fields:**
- Name (text input, required)
- One input per parameter from `manifestConfig`, typed appropriately (string → text, int/float → number, bool → checkbox)
- Pre-filled with defaults from manifest

**On submit:** POST to create endpoint, close modal on success, show alert.

### Step 4.3: Wire into `dashboard/src/pages/AlgorithmDetail.tsx`

- Import `ParameterSetsSection`
- Render between the Details card and Config Schema sections
- Pass `algorithmId` and `manifestConfig` props

### Step 4.4: Implement Export/Import in ParameterSetsSection

- **Export button:** calls `exportParameterSets()`, triggers browser file download of the returned JSON blob
- **Import button:** opens a file input, reads the selected `.json` file, parses it, calls `importParameterSets()`, shows alert with imported/skipped counts

---

## Phase 5: Deploy & Backtest Modal Integration

### Step 5.1: Modify Deploy modal in `AlgorithmDetail.tsx`

- Add `useParameterSets(algorithmId)` query
- Add a "Load from parameter set" `<select>` dropdown above the existing config textarea
- Options: empty option + each set formatted as `"Name (hash)"`
- On select: populate the config textarea with `JSON.stringify(set.config_values, null, 2)`
- Add hidden `parameter_set_id` to the form data sent to create instance

### Step 5.2: Modify `RunBacktestModal.tsx`

- Accept new prop: `parameterSets: ParameterSet[]`
- Accept new optional prop: `preloadSetId?: string` (for the per-row Backtest button)
- Add "Load from parameter set" dropdown (same pattern as deploy)
- On select: populate `configOverrides` state from the set's `config_values`
- If `preloadSetId` is provided, auto-select that set on open
- Include `parameter_set_id` in the backtest create request

### Step 5.3: Create `BacktestAllModal.tsx`

Simplified modal for batch-backtesting all parameter sets.

**Fields:**
- Date range start/end (date pickers, same as RunBacktestModal)
- Initial cash (number input, default 100,000)
- Fee preset (dropdown, same as RunBacktestModal)

**On submit:**
- Fetch all parameter sets for the algorithm
- For each set, call `POST /api/backtest-runs` with the shared date range/cash/fees and the set's `config_values` + `parameter_set_id`
- Show progress: "Running 3 of 5 backtests..."
- On complete: show summary alert, invalidate parameter-sets query to refresh metrics

### Step 5.4: Wire Backtest All and per-row actions

In `ParameterSetsSection`:
- "Backtest All" button opens `BacktestAllModal`
- Per-row "Backtest" button opens `RunBacktestModal` with `preloadSetId` set
- Per-row "Deploy" button opens the existing deploy modal (could pre-select the set — or just open the modal; keep it simple for now)

---

## Phase 6: Test-Algo Config Update

### Step 6.1: Update `quilt-trader-test-algo/quilt.yaml`

Add `data_source` parameter to the SMA crossover algorithm's config:

```yaml
config:
  parameters:
    - name: symbol
      type: string
      default: SPY
      description: Underlying to trade.
    - name: data_source
      type: string
      default: polygon
      description: Market data provider (polygon, thetadata, etc.)
    - name: fast_window
      type: int
      default: 10
    - name: slow_window
      type: int
      default: 30
    - name: target_allocation_pct
      type: float
      default: 0.95
```

### Step 6.2: Update `quilt-trader-test-algo/algorithm.py`

Read `data_source` from config in `on_start()` and pass it to `ctx.market_data()` calls:

```python
self.data_source: str = config.get("data_source", "polygon")
# ...
bars = ctx.market_data(self.symbol, timeframe="1day", bars=self.slow_window + 1,
                       source=self.data_source)
```

Commit and push both changes to the test-algo repo, then update the algorithm in quilt-trader.

---

## Execution Order & Dependencies

```
Phase 1 (DB model + migration) ─────────────────┐
                                                  │
Phase 2 (API endpoints) ── depends on Phase 1 ───┤
                                                  │
Phase 3 (Frontend API layer) ── depends on Phase 2│
                                                  ▼
Phase 4 (Parameter Sets UI) ── depends on Phase 3
                                                  │
Phase 5 (Deploy/Backtest integration) ── depends on Phase 4
                                                  │
Phase 6 (Test-algo config update) ── independent, can run anytime
```

Phases 1 → 2 → 3 → 4 → 5 are sequential (each builds on the previous). Phase 6 is independent and can be done in parallel with any other phase.
