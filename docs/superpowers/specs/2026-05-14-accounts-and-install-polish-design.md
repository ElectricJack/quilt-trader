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

Response (HTTP 200 if all legs filled, HTTP 207 if partial):

```python
{
  "position_id": str | None,                # null if no legs filled
  "legs": [
    {"index": int, "status": "filled" | "rejected", "filled_price": float | None,
     "fees": float | None, "error": str | None, "broker_order_id": str | None}
  ],
  "partial_fill": bool,
}
```

### Server-side flow

1. Load account. If `account.locked_by is not None`, return **423 Locked** with body `{"locked_by": instance_id, "instance_name": ...}`.
2. Validate every leg's `asset_type` is in `account.supported_asset_types`. Return 422 on mismatch.
3. Validate options legs include `expiry`, `strike`, `right`. Return 422 on missing fields.
4. Decrypt creds, construct broker adapter (existing `_adapter_for_account` helper).
5. **Per-broker symbol composition.** Extend `BrokerAdapter` with a `compose_symbol(leg: LegSpec) -> str` method (default implementation returns `leg.symbol` unchanged for equities/crypto). `AlpacaAdapter` and `TradierAdapter` each override it to produce their broker-specific options symbol from `(symbol, expiry, strike, right)` — Alpaca uses OCC (`SPY240620C00500000`), Tradier uses a near-identical OCC variant. The route handler does NOT format symbols itself; it calls `adapter.compose_symbol(leg)` and passes the result to `submit_order`.
6. **For each leg (sequential):** call `adapter.submit_order(symbol=composed, side, quantity, order_type, limit_price)`. On exception, record the leg as rejected and continue.
7. After all legs: if at least one filled, persist:
   - One `TradeLog` per filled leg (`source="manual"`, `asset_type` from the leg).
   - One `Position` row with `legs=[...]` containing the filled legs only, `strategy_type` from request, `status="open"`, `net_cost=sum_of_signed_costs`, `metadata_={"partial_fill": True}` if any leg failed.
8. Return per-leg results plus the new position id.

**Idempotency / dedup:** None needed for v1. Re-submitting the form produces a new position.

**Spread atomicity (deferred):** Both Alpaca and Tradier support native multi-leg options tickets that fill atomically. v1 submits legs sequentially through the existing `submit_order` interface — simpler, sufficient for manual single-shot entry. A future change can extend `BrokerAdapter` with a `submit_multileg_order` method (and Spec C, the strategy builder, will surface it). Document the limitation in the response: when `partial_fill=true`, the dialog tells the user "Legs were filled individually; close the partial fill manually if needed."

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
- Native atomic multi-leg broker tickets (deferred to a follow-up).

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

### Server-side changes

**1. Worker self-reports tailscale IP.**

Extend the worker's heartbeat payload from `{type, worker_id}` → `{type, worker_id, tailscale_ip?}`. The worker reads its IP once on startup (via `subprocess.run(["tailscale", "ip", "-4"], ...)` or equivalent) and includes it in every heartbeat; falls back to omitting the field if unavailable.

`handle_worker_message` heartbeat branch (`coordinator/api/websocket.py:71`):

```python
if worker:
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
```

**2. Make `tailscale_ip` optional at registration.**

`WorkerCreate.tailscale_ip: Optional[str] = None`. Schema/migration: `Worker.tailscale_ip: Mapped[Optional[str]]` (currently `nullable=False`). Backfill existing rows where needed.

**3. No change to `install-worker.sh`** — it already runs `tailscale up`. We just add the IP reporting in `worker/agent.py`'s heartbeat code path.

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
- Changing how the install script works on the Pi (no script changes in this spec; only worker agent adds tailscale IP to heartbeats).

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
  - All-legs filled → 200 + Position created + per-leg TradeLog inserted.
  - Mid-list leg fails → 207 + Position with `partial_fill=True` + only filled legs persisted.
- **Algo install via URL:** unit test the manifest-fetch fallback chain (public → private → reject). API test using a fake `quilt.yaml` over a mocked HTTP layer. End-to-end manual smoke against a real public quilt algorithm repo.
- **Worker install dialog:** existing heartbeat handler test extended to assert the `worker_connected` broadcast fires on offline→online transition. Frontend component test for the dialog's state machine. Manual smoke against a real Pi to confirm auto-close.

### Compatibility

- Existing accounts with messy `supported_asset_types` (e.g. unrecognized entries from the old free-text input) keep working at read time, but `PATCH` will require the user to re-check the boxes from the new catalog. Acceptable — the dataset is small.
- The legacy `/api/github/repos` and `/api/github/install` endpoints stay until a follow-up cleanup. No active callers after this spec lands.
- `Worker.tailscale_ip` nullability change is forward-compatible; existing UI keeps rendering the IP when present.

### Implementation order

1. Asset-type catalog (foundation — both account form and position-open form depend on it).
2. Open-position UI (depends on 1).
3. Worker install dialog (independent).
4. Algo install via URL (independent).

Each can land in its own PR.
