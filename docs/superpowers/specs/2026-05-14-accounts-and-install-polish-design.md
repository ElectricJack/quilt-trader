---
title: Accounts & Install Polish
status: design
date: 2026-05-14
---

# Accounts & Install Polish — Design

This spec covers four loosely-related improvements that share a single foundation (a broker-driven asset-type catalog) but are otherwise independent:

1. **Asset-type catalog** — replace the free-text asset-types input with broker-aware checkboxes, and expose the same catalog to the order ticket.
2. **Open-position UI** — a new multi-leg position-open form on `AccountDetail`, submitting through the coordinator's broker adapter. Honors `Account.locked_by`.
3. **Algo install via URL** — replace the "pick from my GitHub repos" dropdown with a single repo-URL input that pre-validates `quilt.yaml` before cloning. Supports both public repos and private repos via configured PAT.
4. **Worker install dialog** — register-then-wait flow in a single dialog that pushes the bash one-liner, waits for the worker's first WS heartbeat, and auto-closes.

Two larger features are explicitly **out of scope** and tracked separately:

- **Spec B (live data subscriptions + multi-dataset compare)** — broker-fed live subscriptions, "live" datasets organized by source alongside historical, and side-by-side comparison.
- **Spec C (options strategy builder/visualizer)** — optionstrat.com-style P&L diagram and strategy designer. Spec C will integrate with the multi-leg ticket built here.

---

## 1. Asset-Type Catalog

### Motivation

`Account.supported_asset_types` is a `JSON list`. The dashboard currently surfaces it as a comma-separated text input (`dashboard/src/pages/Accounts.tsx:434-440`). This is error-prone: typos silently cap algorithm compatibility, and there's no canonical vocabulary. Both account creation and the new order ticket need a controlled vocabulary keyed by broker.

### Server-side

**New module:** `coordinator/services/asset_catalog.py`

```python
BROKER_ASSET_TYPES: dict[str, list[str]] = {
    "alpaca":  ["equities", "options", "crypto"],
    "tradier": ["equities", "options"],
}

def asset_types_for_broker(broker_type: str) -> list[str]:
    if broker_type not in BROKER_ASSET_TYPES:
        raise ValueError(f"Unknown broker: {broker_type}")
    return list(BROKER_ASSET_TYPES[broker_type])
```

**New endpoint:** `GET /api/brokers/{broker_type}/asset-types`

Returns `{"asset_types": [...]}`. 404 if broker unknown. Lives in a new `coordinator/api/routes/brokers.py`.

**Server-side validation** on `POST /api/accounts` and `PATCH /api/accounts/{id}`:

- `supported_asset_types` must be a non-empty subset of `BROKER_ASSET_TYPES[broker_type]`. Reject with 422 listing the disallowed entries.
- For PATCH, broker is not mutable today (UI disables it; server should also reject any attempt to change `broker_type`).

### UI

`AccountsAdd` / `AccountsEdit` (currently both in `dashboard/src/pages/Accounts.tsx`; will need a small refactor to share the asset-types control):

- New `FormField label="Supported Asset Types"` renders as a checkbox group.
- Catalog comes from a new `useBrokerAssetTypes(broker_type)` hook. The query is enabled only once a broker is selected; on broker change in the Add form, the previously-checked set is discarded.
- Zod schema: `supported_asset_types: z.array(z.string()).min(1, "Select at least one asset type")`. Replace the existing comma-split free-text path in `handleCreate`/`handleEdit`.
- The compact details row on `AccountDetail.tsx` (`DetailItem label="Assets"`) keeps rendering as a comma-joined list — that's a display concern, no change needed.

### Out of scope

- Adding new brokers (none planned in this spec).
- "Account features" and "options_level" controls — those stay as today.

---

## 2. Open-Position UI (Multi-Leg)

### Motivation

Today there's no way to open a position from the dashboard. The Position model already supports multi-leg via `strategy_type` + `legs[]`. The user wants manual order entry for any asset type the account supports, including options spreads.

### New endpoint

`POST /api/accounts/{account_id}/positions/open`

```python
class LegSpec(BaseModel):
    symbol: str                        # underlying for options; ticker for equities/crypto
    asset_type: str                    # "equities" | "options" | "crypto"
    side: str                          # "buy" | "sell"
    quantity: float
    # options-only:
    expiry: str | None = None          # YYYY-MM-DD
    strike: float | None = None
    right: str | None = None           # "call" | "put"

class OpenPositionRequest(BaseModel):
    legs: list[LegSpec]                # 1..N
    strategy_type: str = "single"      # free-form label: "single" | "vertical" | "iron_condor" | "custom" | ...
    order_type: str = "market"         # "market" | "limit"
    limit_price: float | None = None   # required if order_type == "limit"
```

Response (HTTP 200 on full fill, HTTP 207 only on the rare partial-fill fallback path):

```python
{
  "position_id": str | None,                # null if nothing filled
  "broker_order_id": str | None,            # parent multi-leg order id when atomic
  "legs": [
    {"index": int, "status": "filled" | "rejected" | "pending",
     "filled_price": float | None, "fees": float | None,
     "error": str | None, "broker_order_id": str | None}
  ],
  "atomic": bool,                           # true if filled via native multi-leg endpoint
  "partial_fill": bool,                     # true only if atomic=false AND some legs failed
}
```

### BrokerAdapter additions

Two new methods on `worker/broker_adapter.py`:

```python
class BrokerAdapter(ABC):
    def supports_multileg_orders(self, legs: list[LegSpec]) -> bool:
        """Whether this adapter can submit `legs` as a single atomic ticket."""
        return False

    def compose_symbol(self, leg: LegSpec) -> str:
        """Format a leg into a broker-specific symbol (OCC for options)."""
        return leg.symbol

    def submit_multileg_order(
        self, legs: list[LegSpec], order_type: str, limit_price: float | None
    ) -> "MultilegOrderResult":
        """Submit `legs` as one atomic broker order. Raises if unsupported."""
        raise NotImplementedError
```

- `AlpacaAdapter`: implements both. `supports_multileg_orders` returns `True` when all legs are options on the same underlying (the constraint Alpaca's MLEG order class enforces). `submit_multileg_order` POSTs to `/v2/orders` with `order_class=mleg` and a `legs[]` array, returns parent order id + per-leg fills.
- `TradierAdapter`: implements both. `supports_multileg_orders` returns `True` when all legs are options on the same underlying. `submit_multileg_order` POSTs to `/v1/accounts/{id}/orders` with `class=multileg` and per-leg `option_symbol_N/side_N/quantity_N` fields, returns the parent order id.
- `MockBrokerAdapter`: `supports_multileg_orders` returns `False` so tests exercise the fallback path.

### Server-side flow

1. Load account. If `account.locked_by is not None`, return **423 Locked** with body `{"locked_by": instance_id, "instance_name": ...}`.
2. Validate every leg's `asset_type` is in `account.supported_asset_types`. Return 422 on mismatch.
3. Validate options legs include `expiry`, `strike`, `right`. Return 422 on missing fields.
4. Decrypt creds, construct broker adapter (existing `_adapter_for_account` helper).
5. **Dispatch:**
   - If `len(legs) > 1 and adapter.supports_multileg_orders(legs)` → call `submit_multileg_order(...)`. On success: one `TradeLog` per filled leg, one `Position` row (`metadata_.broker_order_id` = parent id), `atomic=true` in the response. On broker rejection: 422 with the broker's error message; nothing persisted (atomic means all-or-nothing). **No partial-fill state possible on this path.**
   - Else → sequential fallback: for each leg, call `adapter.submit_order(adapter.compose_symbol(leg), side, quantity, order_type, limit_price)`. On exception, record the leg as rejected and continue. After all legs, if at least one filled, persist one `Position` with `metadata_.partial_fill = True` if any failed. Return 207 if partial. This path is used for single-leg orders, mixed-broker scenarios, or any case `supports_multileg_orders` returns False.
6. Return the response shape above.

**Idempotency / dedup:** None needed for v1. Re-submitting the form produces a new position.

**Why this is simpler than my first draft.** Sequential individual fills required us to invent partial-rollback semantics that don't actually match how options spreads behave at the broker layer. Native multi-leg endpoints (Alpaca's MLEG, Tradier's `class=multileg`) fill atomically — either the whole spread fills at the requested net debit/credit, or nothing does. The complex 207/partial-fill response only applies to the fallback path (single-leg, equities, or mocks).

### UI

`AccountDetail.tsx`:

- New **Open Position** button next to Refresh/Sync in the page header.
- If `account.locked_by` is set: button is disabled, shows a lock icon, and clicking it opens a tooltip/inline note: "Locked by algorithm [instance link]. Stop the algo to open positions manually." A new "View Algorithm" link in the existing lock badge goes to `/instances/{locked_by}`.
- Clicking the (unlocked) button opens a new `OpenPositionModal`:
  - **Strategy preset** dropdown: Single, Vertical Spread, Straddle, Strangle, Iron Condor, Custom. Selecting a preset prefills a legs table with the right number of rows + sensible defaults (e.g. Vertical Spread → 2 legs, both same expiry/right, opposite sides). The preset value is passed through as `strategy_type`; it's a label, not enforced.
  - **Legs table** with add/remove row buttons. Per-row fields (rendered based on `asset_type` for that row):
    - asset_type select (filtered to `account.supported_asset_types`)
    - symbol input
    - side (buy/sell)
    - quantity
    - For options: expiry (date picker), strike (number), right (call/put)
  - **Order type:** market or limit (limit shows net debit/credit input).
  - **Estimated net cost/credit** summary at the bottom (computed client-side from filled-in fields when prices are available; otherwise hidden).
  - **Submit** button.
- After submit, the modal shows a per-leg result view:
  - Green check + filled price/fees for filled legs.
  - Red X + error message for rejected legs.
  - A "Done" button that closes the modal and triggers a `useBrokerInfo` refetch (to reflect the new position).

### Out of scope

- Closing positions (separate workflow — already partly handled by sell-side trades).
- Strategy visualization (Spec C).
- Lumibot order integration. Lumibot's footprint in this repo is the CLI backtest harness only (`sdk/cli/backtest.py`); the live path uses the project's own `BrokerAdapter` against the broker SDKs directly. Adopting Lumibot's broker layer to gain its multi-leg machinery would be a larger architectural swap than calling Alpaca's MLEG and Tradier's `class=multileg` endpoints ourselves.

---

## 3. Algo Install via URL

### Motivation

The current install flow uses `/api/github/repos` which requires a configured GitHub PAT (`coordinator/api/routes/github.py:27-41`). When the PAT is missing, the dropdown returns 400 and the install modal is unusable. The user wants to paste a GitHub URL and have the system verify it's a quilt-trader algorithm by reading the manifest *before* doing anything destructive (cloning, venv creation, dependency install). This also enables installing public algorithms without configuring a PAT at all.

### New endpoint

`POST /api/algorithms/install-from-url`

```python
class InstallFromUrlRequest(BaseModel):
    repo_url: str  # https://github.com/owner/repo[.git]
```

### Server-side flow

1. Parse `owner/repo` from the URL using the existing `_full_name_from_url` helper (`coordinator/api/routes/algorithms.py:169`). Reject 400 if unparseable.
2. **Pre-flight manifest fetch.** Try, in order:
   - `GET https://raw.githubusercontent.com/{owner}/{repo}/HEAD/quilt.yaml` (no auth).
   - On 404: if a GitHub PAT is configured (Setting key `github_pat`), `GET https://api.github.com/repos/{owner}/{repo}/contents/quilt.yaml` with the PAT, decode base64 content.
   - Otherwise reject 400 with: `"Repository not found or quilt.yaml missing. If the repo is private, configure a GitHub PAT in Settings."`
3. Parse the YAML via `QuiltManifest.from_string` (`sdk/manifest.py:46`). If `manifest.type != "algorithm"`, reject 422 with `"That repo is a {manifest.type}, not an algorithm."`
4. Only after validation passes: resolve `clone_url`:
   - Public: `https://github.com/{owner}/{repo}.git`
   - Private: PAT-authenticated URL `https://{pat}@github.com/{owner}/{repo}.git` (or `git -c http.extraHeader=...` to avoid embedding the PAT in the URL stored anywhere).
5. Run the existing `PackageManager.clone_repo` → `create_venv` → `install_requirements` → `validate_package` chain. Insert `Algorithm` row populated from the validated manifest. Persist `commit_hash` via `get_commit_hash`.

### UI changes (Algorithms.tsx)

- Replace the `useGithubRepos` dropdown with a single `repo_url` text input. Zod schema:
  ```ts
  installSchema = z.object({
    repo_url: z.string().url().refine(
      (v) => /^https?:\/\/github\.com\/[^/]+\/[^/]+/.test(v),
      "Must be a GitHub repo URL"
    ),
  });
  ```
- Submit button label: "Install". While installing, show a generic "Installing… (this may take ~60s)" message. Staged status (clone → venv → deps) is a future enhancement; not in this spec.
- `useInstallAlgorithm` calls the new endpoint (`POST /api/algorithms/install-from-url`).
- The existing `POST /api/github/install` (PAT-only, full_name-based) and `GET /api/github/repos` endpoints stay in the codebase for now. **Deletion is deferred to a cleanup pass** to keep this spec focused; nothing in the UI will call them after this change.

### Out of scope

- Removing the legacy `/api/github/*` endpoints (separate cleanup commit).
- Supporting non-GitHub forges.

---

## 4. Worker Install Dialog (Push-based)

### Motivation

Today the flow is: register the worker (modal closes), then a `WorkerInstallCommand` panel renders inline on the Workers page (`dashboard/src/pages/Workers.tsx:130`). The user has to keep that page open, copy the command, SSH into the Pi, paste, and visually correlate with eventual heartbeats in the worker list. The user wants a single modal that holds the command and auto-closes when the worker actually phones home.

Additionally, the current `Worker` model requires `tailscale_ip` at registration time — but the user doesn't know the Tailscale IP before the Pi is provisioned. The Pi self-discovers it via `tailscale ip --4` once `tailscale up` runs.

### Pre-existing bug to fix in this spec

The current heartbeat path is **broken end-to-end** and the bug surfaces only when we start depending on `Worker.status` (which this spec does). Tracing it:

- `worker/agent.py:43-44`: `send_heartbeat` sends `{"type": "heartbeat", "worker_name": self.worker_name, ...}` — **no `worker_id`**.
- `coordinator/api/websocket.py:71-86`: handler reads `worker_id = data.get("worker_id")` (always `None`), then the `if worker_id:` block never executes. `Worker.last_heartbeat` and `Worker.status` are never updated by the running system today.
- Root cause: `worker/config.py` has `worker_name` but no `worker_id` field, and `scripts/install-worker.sh:117-122` writes the env file without `QTW_WORKER_ID`. The worker literally doesn't know its own UUID — only its name.

The dashboard tolerates this only because nothing currently reads those fields meaningfully (`relativeTime(w.last_heartbeat)` just prints "never" and the install-claim POST is enough to flip `install_status` to `claimed` for the UI's existing pending/online states).

### Server-side changes

**1. Fix the worker-id wiring.** Three small changes:

- `scripts/install-worker.sh`: add `QTW_WORKER_ID=${WORKER_ID}` to the env file written at lines 117-122. `WORKER_ID` is already in scope (required env var, line 45-47). One-line addition.
- `worker/config.py` `WorkerConfig`: add `worker_id: str = ""` (env var `QTW_WORKER_ID`).
- `worker/agent.py`: `WorkerAgent.__init__` takes `worker_id` alongside `worker_name`; `worker/main.py` passes both from config. `send_heartbeat` payload becomes:
  ```python
  {"type": "heartbeat", "worker_id": self.worker_id,
   "worker_name": self.worker_name, "tailscale_ip": self.tailscale_ip,
   "timestamp": datetime.now(timezone.utc).isoformat()}
  ```

**2. Worker self-reports tailscale IP.**

`WorkerAgent` discovers the IP once on startup via `subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=2)` and caches it. Falls back to `None` if Tailscale isn't installed or the call fails (so the worker still functions when run locally outside a Tailscale-managed Pi). The cached value is included in every heartbeat.

**3. Coordinator heartbeat handler (`coordinator/api/websocket.py:71`)** — now it actually finds the row, and we add the broadcast:

```python
elif msg_type == "heartbeat":
    worker_id = data.get("worker_id")
    await websocket.send_json({"type": "heartbeat_ack"})
    if not worker_id:
        return
    try:
        container = get_container()
        async with container.session_factory() as session:
            result = await session.execute(select(Worker).where(Worker.id == worker_id))
            worker = result.scalar_one_or_none()
            if not worker:
                return
            prior_status = worker.status
            worker.last_heartbeat = datetime.now(timezone.utc)
            worker.status = "online"
            if data.get("tailscale_ip"):
                worker.tailscale_ip = data["tailscale_ip"]
            await session.commit()

            if prior_status != "online":
                await manager.broadcast_to_dashboards({
                    "type": "worker_connected",
                    "worker_id": worker.id,
                    "name": worker.name,
                    "tailscale_ip": worker.tailscale_ip,
                    "install_status": worker.install_status,
                })
    except Exception:
        logger.exception("Failed to update heartbeat for worker %s", worker_id)
```

**4. Make `tailscale_ip` optional at registration.**

`WorkerCreate.tailscale_ip: Optional[str] = None`. Schema/migration: `Worker.tailscale_ip: Mapped[Optional[str]]` (currently `nullable=False`). Backfill existing rows where needed.

### UI changes (Workers.tsx + new component)

Replace the current "register + inline command + dismiss" UX with a single `WorkerInstallDialog`:

**State machine:**

```
[ form ] --register--> [ waiting ] --worker_connected event--> [ connected (1.5s) ] --auto-close--> [ closed ]
                          |  ^
                          |  | regenerate-token (stays in waiting; new token, new command rendered)
                          +--+

       cancel (from any state)  -->  [ closed ]   (worker row preserved in install_status=pending if it was created)
```

**Step 1 — Form.** Fields: **Name** (required), **Max algorithms** (default 2). No tailscale_ip field. Submit calls `POST /api/workers` → on success, dialog advances; does NOT close.

**Step 2 — Waiting.** Render:

- The bash one-liner (existing markup from `WorkerInstallCommand`) with a Copy button. The user is told to replace `tskey-CHANGE-ME` with a real Tailscale key.
- A status row:
  - `⏳ Waiting for worker to connect…` (default)
  - `⏳ Install claimed, waiting for first heartbeat…` (set when polled GET shows `install_status==="claimed"` but `status !== "online"` — fallback when WS broadcast was missed)
- A "Regenerate token" link.
- A "Cancel" button that closes the dialog without rolling back. The worker row stays in `install_status=pending`; user can resume from the Workers list (existing UI already supports this).

**Step 3 — Connected.** Triggered by either:
- WS message `worker_connected` with matching `worker_id` (primary path).
- A polled `GET /api/workers/{id}` showing `status === "online"` (fallback path; poll every 5s while in Step 2).

State flips to `✓ Connected!` for ~1.5s, then auto-closes. The worker list (`useWorkers`) is invalidated so the new worker is visible immediately.

**Hook:** `useWorkerConnectedEvent(workerId)` subscribes to the dashboard WS and resolves when a `worker_connected` message with matching `worker_id` arrives. Implemented on top of the existing dashboard WS plumbing.

### Out of scope

- Streaming systemd journal logs into the dialog (could be a future enhancement).
- Reworking the Pi-side install script's broader structure. This spec adds exactly one line (`QTW_WORKER_ID=${WORKER_ID}` to the env file) — everything else the script does (Tailscale, package download, venv, systemd) stays as-is.

---

## Cross-cutting concerns

### Database migrations

One alembic migration covering:

- `workers.tailscale_ip` — drop NOT NULL.
- No other schema changes; all the new UI behavior is additive over existing tables/columns.

### Testing strategy

Per section, plus integration coverage:

- **Asset-type catalog:** unit tests on `asset_catalog.asset_types_for_broker`. API test on the new `/api/brokers/{broker_type}/asset-types` (200 happy path, 404 unknown). API test on `POST /api/accounts` rejecting a disallowed asset type.
- **Open-position UI:** API tests for the new endpoint:
  - 423 when account is locked.
  - 422 on disallowed asset_type / missing options fields.
  - **Atomic-path success** (mock adapter set to `supports_multileg_orders=True`): 200 + Position with `metadata_.broker_order_id` set + atomic=true in response.
  - **Atomic-path broker rejection:** 422 + nothing persisted.
  - **Fallback-path success** (mock adapter returns `False`, all submit_order calls succeed): 200 + Position created + per-leg TradeLog inserted.
  - **Fallback-path partial fill** (mid-list submit_order raises): 207 + Position with `metadata_.partial_fill=True` + only filled legs persisted.
  - Per-broker OCC symbol composition: unit tests for `AlpacaAdapter.compose_symbol` and `TradierAdapter.compose_symbol` covering call/put, varying strikes.
- **Algo install via URL:** unit test the manifest-fetch fallback chain (public → private → reject). API test using a fake `quilt.yaml` over a mocked HTTP layer. End-to-end manual smoke against a real public quilt algorithm repo.
- **Worker install dialog:**
  - **Pre-existing-bug regression:** dedicated test that a heartbeat message with `worker_id` actually finds the row, updates `status="online"` and `last_heartbeat`, and writes `tailscale_ip` when present. Today this would fail; that's the point.
  - Heartbeat handler test extended to assert the `worker_connected` broadcast fires on offline→online transition and is suppressed on subsequent heartbeats while already online.
  - Frontend component test for the dialog's state machine (form → waiting → connected → auto-close), driven by a fake WS event source.
  - Manual smoke against a real Pi to confirm auto-close end-to-end.

### Compatibility

- Existing accounts with messy `supported_asset_types` (e.g. unrecognized entries from the old free-text input) keep working at read time, but `PATCH` will require the user to re-check the boxes from the new catalog. Acceptable — the dataset is small.
- The legacy `/api/github/repos` and `/api/github/install` endpoints stay until a follow-up cleanup. No active callers after this spec lands.
- `Worker.tailscale_ip` nullability change is forward-compatible; existing UI keeps rendering the IP when present.
- **Already-installed workers won't have `QTW_WORKER_ID` in their env file.** They'll keep failing the heartbeat-update path until the env file is patched (or they're reinstalled with the updated script). Mitigation: a one-line manual fix on the Pi (`sudo sh -c 'echo QTW_WORKER_ID=... >> /etc/quilt-trader-worker.env && systemctl restart quilt-trader-worker'`) — document this in the spec's implementation notes. The user has one Pi today, so no migration tooling needed.

### Implementation order

1. Asset-type catalog (foundation — both account form and position-open form depend on it).
2. Open-position UI (depends on 1).
3. Worker install dialog (independent).
4. Algo install via URL (independent).

Each can land in its own PR.
