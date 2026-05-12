# Plan 5: Dashboard

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the React + TypeScript dashboard for QuiltTrader — the web UI served by the coordinator that provides account management, algorithm lifecycle controls, worker monitoring, data management, backtest comparison views, notification configuration, and system settings.

**Architecture:** A React SPA built with Vite, using TanStack Query for server state, Zustand for UI state, Tailwind CSS for styling, and Lightweight Charts for financial visualizations. The dashboard communicates with the coordinator via REST API and WebSocket for real-time updates. It is served as static files by FastAPI in production.

**Tech Stack:** React 18+, TypeScript, Vite, Tailwind CSS, TanStack Query, TanStack Table, Zustand, React Hook Form + Zod, Lightweight Charts (TradingView), Lucide React, React Router

---

## File Map

| File | Responsibility |
|------|---------------|
| `dashboard/package.json` | Node dependencies |
| `dashboard/vite.config.ts` | Vite configuration with API proxy |
| `dashboard/tsconfig.json` | TypeScript configuration |
| `dashboard/tailwind.config.js` | Tailwind configuration |
| `dashboard/postcss.config.js` | PostCSS for Tailwind |
| `dashboard/index.html` | HTML entry point |
| `dashboard/src/main.tsx` | React entry point |
| `dashboard/src/App.tsx` | Root component with router and providers |
| `dashboard/src/api/client.ts` | Typed API client (fetch wrapper) |
| `dashboard/src/api/hooks.ts` | TanStack Query hooks for all API endpoints |
| `dashboard/src/api/websocket.ts` | WebSocket connection manager |
| `dashboard/src/stores/ui.ts` | Zustand UI state store |
| `dashboard/src/types/index.ts` | TypeScript types matching API schema |
| `dashboard/src/components/Layout.tsx` | App shell — sidebar, header, main content area |
| `dashboard/src/components/StatusBadge.tsx` | Reusable status indicator component |
| `dashboard/src/components/MetricsCard.tsx` | Reusable metrics display card |
| `dashboard/src/components/EquityCurve.tsx` | Lightweight Charts equity curve component |
| `dashboard/src/components/DataTable.tsx` | TanStack Table wrapper for paginated tables |
| `dashboard/src/components/ConfirmDialog.tsx` | Confirmation dialog for destructive actions |
| `dashboard/src/pages/Overview.tsx` | System overview / home page |
| `dashboard/src/pages/Accounts.tsx` | Account list view |
| `dashboard/src/pages/AccountDetail.tsx` | Account detail with positions, manual trading, cash flows |
| `dashboard/src/pages/Algorithms.tsx` | Algorithm library + install from GitHub |
| `dashboard/src/pages/AlgorithmDetail.tsx` | Algorithm detail with instances and aggregate metrics |
| `dashboard/src/pages/InstanceDetail.tsx` | Instance detail with runs, metrics, trade history |
| `dashboard/src/pages/RunDetail.tsx` | Individual run performance report |
| `dashboard/src/pages/Workers.tsx` | Worker management |
| `dashboard/src/pages/Data.tsx` | Market data downloads + scraper management |
| `dashboard/src/pages/Backtests.tsx` | Backtest comparison list |
| `dashboard/src/pages/BacktestDetail.tsx` | Divergence drill-down |
| `dashboard/src/pages/Notifications.tsx` | Discord channel routing config |
| `dashboard/src/pages/Settings.tsx` | System settings |

---

### Task 1: Project Scaffolding

**Files:**
- Create: `dashboard/package.json`
- Create: `dashboard/vite.config.ts`
- Create: `dashboard/tsconfig.json`
- Create: `dashboard/tailwind.config.js`
- Create: `dashboard/postcss.config.js`
- Create: `dashboard/index.html`
- Create: `dashboard/src/main.tsx`

- [ ] **Step 1: Create package.json**

```json
{
  "name": "quilt-trader-dashboard",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview",
    "lint": "eslint src --ext ts,tsx",
    "typecheck": "tsc --noEmit"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.28.0",
    "@tanstack/react-query": "^5.62.0",
    "@tanstack/react-table": "^8.20.0",
    "zustand": "^5.0.0",
    "react-hook-form": "^7.54.0",
    "@hookform/resolvers": "^3.9.0",
    "zod": "^3.24.0",
    "lightweight-charts": "^4.2.0",
    "lucide-react": "^0.460.0",
    "clsx": "^2.1.0"
  },
  "devDependencies": {
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.0",
    "autoprefixer": "^10.4.20",
    "postcss": "^8.4.49",
    "tailwindcss": "^3.4.0",
    "typescript": "^5.6.0",
    "vite": "^5.4.0"
  }
}
```

- [ ] **Step 2: Create vite.config.ts**

```typescript
// dashboard/vite.config.ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/ws": {
        target: "ws://localhost:8000",
        ws: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
```

- [ ] **Step 3: Create tsconfig.json**

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "baseUrl": ".",
    "paths": {
      "@/*": ["src/*"]
    }
  },
  "include": ["src"]
}
```

- [ ] **Step 4: Create tailwind.config.js**

```javascript
// dashboard/tailwind.config.js
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        profit: "#22c55e",
        loss: "#ef4444",
      },
    },
  },
  plugins: [],
};
```

- [ ] **Step 5: Create postcss.config.js**

```javascript
// dashboard/postcss.config.js
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
```

- [ ] **Step 6: Create index.html**

```html
<!-- dashboard/index.html -->
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>QuiltTrader</title>
  </head>
  <body class="bg-gray-950 text-gray-100">
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 7: Create src/main.tsx**

```typescript
// dashboard/src/main.tsx
import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
```

- [ ] **Step 8: Create src/index.css**

```css
/* dashboard/src/index.css */
@tailwind base;
@tailwind components;
@tailwind utilities;
```

- [ ] **Step 9: Install dependencies**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npm install`
Expected: All packages install successfully

- [ ] **Step 10: Commit**

```bash
git add dashboard/
git commit -m "feat(dashboard): scaffold React + Vite + Tailwind project"
```

---

### Task 2: TypeScript Types + API Client

**Files:**
- Create: `dashboard/src/types/index.ts`
- Create: `dashboard/src/api/client.ts`

- [ ] **Step 1: Create TypeScript types**

```typescript
// dashboard/src/types/index.ts
export interface Account {
  id: string;
  name: string;
  broker_type: string;
  supported_asset_types: string[];
  options_level: number | null;
  account_features: string[] | null;
  pdt_mode: string;
  locked_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface Algorithm {
  id: string;
  repo_url: string;
  name: string;
  description: string | null;
  version: string | null;
  commit_hash: string | null;
  required_asset_types: string[] | null;
  required_options_level: number | null;
  required_account_features: string[] | null;
  supported_brokers: string[] | null;
  data_dependencies: DataDependency[] | null;
  config_schema: ConfigSchema | null;
  custom_events: CustomEvent[] | null;
  install_status: string;
  install_error: string | null;
  installed_at: string;
  updated_at: string;
}

export interface DataDependency {
  name: string;
  repo: string;
}

export interface ConfigSchema {
  parameters: ConfigParameter[];
}

export interface ConfigParameter {
  name: string;
  type: string;
  default: unknown;
  description: string;
  min?: number;
  max?: number;
}

export interface CustomEvent {
  name: string;
  description: string;
  severity: string;
}

export interface AlgorithmInstance {
  id: string;
  algorithm_id: string;
  account_id: string;
  worker_id: string;
  status: string;
  active_run_id: string | null;
  config_values: Record<string, unknown> | null;
  persisted_state: Record<string, unknown> | null;
  state_stale: boolean;
  lifetime_metrics: PerformanceMetrics | null;
  created_at: string;
  updated_at: string;
}

export interface AlgorithmRun {
  id: string;
  instance_id: string;
  run_number: number;
  status: string;
  started_at: string;
  stopped_at: string | null;
  starting_equity: number | null;
  ending_equity: number | null;
  net_pnl: number | null;
  unrealized_pnl: number | null;
  total_fees: number;
  total_slippage: number;
  trade_count: number;
  metrics: PerformanceMetrics | null;
  equity_curve: EquityPoint[] | null;
}

export interface EquityPoint {
  timestamp: string;
  equity: number;
}

export interface Worker {
  id: string;
  name: string;
  tailscale_ip: string;
  status: string;
  last_heartbeat: string | null;
  max_algorithms: number;
  created_at: string;
}

export interface PerformanceMetrics {
  total_return_pct: number;
  total_return_dollars: number;
  annualized_return_pct: number;
  time_weighted_return_pct: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  max_drawdown_pct: number;
  max_drawdown_dollars: number;
  max_drawdown_duration_days: number;
  calmar_ratio: number;
  win_rate_pct: number;
  loss_rate_pct: number;
  profit_factor: number;
  avg_win_dollars: number;
  avg_loss_dollars: number;
  largest_win_dollars: number;
  largest_loss_dollars: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  total_fees_dollars: number;
  net_profit_after_fees: number;
  positions_opened: number;
  positions_closed: number;
  positions_open: number;
}

export interface Position {
  id: string;
  instance_id: string | null;
  account_id: string;
  strategy_type: string;
  legs: PositionLeg[];
  status: string;
  opened_at: string;
  closed_at: string | null;
  net_cost: number;
  net_proceeds: number | null;
  net_pnl: number | null;
  unrealized_pnl: number | null;
  total_fees: number;
}

export interface PositionLeg {
  symbol: string;
  side: string;
  quantity: number;
  avg_cost: number;
  asset_type: string;
}

export interface TradeLogEntry {
  id: string;
  group_id: string;
  instance_id: string | null;
  account_id: string;
  source: string;
  timestamp: string;
  symbol: string;
  asset_type: string;
  side: string;
  quantity: number;
  order_type: string;
  filled_price: number;
  fees: number;
  slippage: number | null;
  is_day_trade: boolean;
}

export interface SystemEvent {
  id: string;
  source_type: string;
  source_id: string | null;
  event_type: string;
  severity: string;
  payload: Record<string, unknown> | null;
  timestamp: string;
  routed_to_discord: boolean;
  discord_channel: string | null;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface RepoInfo {
  name: string;
  full_name: string;
  description: string;
  clone_url: string;
  html_url: string;
}

export interface SettingsStatus {
  github_pat_set: boolean;
  discord_bot_token_set: boolean;
  polygon_api_key_set: boolean;
  theta_data_set: boolean;
}
```

- [ ] **Step 2: Create API client**

```typescript
// dashboard/src/api/client.ts
const BASE_URL = "/api";

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
    ...options,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: "Unknown error" }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json();
}

export const api = {
  // Accounts
  listAccounts: () => request<Account[]>("/accounts"),
  getAccount: (id: string) => request<Account>(`/accounts/${id}`),
  createAccount: (data: AccountCreate) =>
    request<Account>("/accounts", { method: "POST", body: JSON.stringify(data) }),
  updateAccount: (id: string, data: Partial<AccountCreate>) =>
    request<Account>(`/accounts/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  deleteAccount: (id: string) =>
    request<void>(`/accounts/${id}`, { method: "DELETE" }),

  // Workers
  listWorkers: () => request<Worker[]>("/workers"),
  getWorker: (id: string) => request<Worker>(`/workers/${id}`),
  createWorker: (data: WorkerCreate) =>
    request<Worker>("/workers", { method: "POST", body: JSON.stringify(data) }),
  updateWorker: (id: string, data: Partial<WorkerCreate>) =>
    request<Worker>(`/workers/${id}`, { method: "PATCH", body: JSON.stringify(data) }),
  deleteWorker: (id: string) =>
    request<void>(`/workers/${id}`, { method: "DELETE" }),

  // Algorithms
  listAlgorithms: () => request<Algorithm[]>("/algorithms"),
  getAlgorithm: (id: string) => request<Algorithm>(`/algorithms/${id}`),
  deleteAlgorithm: (id: string) =>
    request<void>(`/algorithms/${id}`, { method: "DELETE" }),
  listInstances: (algoId: string) =>
    request<AlgorithmInstance[]>(`/algorithms/${algoId}/instances`),
  createInstance: (algoId: string, data: InstanceCreate) =>
    request<AlgorithmInstance>(`/algorithms/${algoId}/instances`, {
      method: "POST",
      body: JSON.stringify(data),
    }),
  getInstance: (id: string) => request<AlgorithmInstance>(`/instances/${id}`),

  // Events
  listEvents: (params?: EventParams) => {
    const searchParams = new URLSearchParams();
    if (params?.event_type) searchParams.set("event_type", params.event_type);
    if (params?.severity) searchParams.set("severity", params.severity);
    if (params?.limit) searchParams.set("limit", String(params.limit));
    if (params?.offset) searchParams.set("offset", String(params.offset));
    const qs = searchParams.toString();
    return request<PaginatedResponse<SystemEvent>>(`/events${qs ? `?${qs}` : ""}`);
  },

  // Settings
  getSettings: () => request<SettingsStatus>("/settings"),
  setGithubPat: (value: string) =>
    request<SettingsStatus>("/settings/github-pat", {
      method: "PUT",
      body: JSON.stringify({ value }),
    }),
  setDiscordToken: (value: string) =>
    request<SettingsStatus>("/settings/discord-token", {
      method: "PUT",
      body: JSON.stringify({ value }),
    }),
  setPolygonKey: (value: string) =>
    request<SettingsStatus>("/settings/polygon-key", {
      method: "PUT",
      body: JSON.stringify({ value }),
    }),
  setThetaData: (username: string, password: string) =>
    request<SettingsStatus>("/settings/theta-data", {
      method: "PUT",
      body: JSON.stringify({ username, password }),
    }),

  // GitHub
  listRepos: () => request<RepoInfo[]>("/github/repos"),
  installAlgorithm: (full_name: string) =>
    request<Algorithm>("/github/install", {
      method: "POST",
      body: JSON.stringify({ full_name }),
    }),

  // Health
  health: () => request<{ status: string; version: string }>("/health"),
};

// Request body types
interface AccountCreate {
  name: string;
  broker_type: string;
  credentials: Record<string, string>;
  supported_asset_types: string[];
  options_level?: number;
  account_features?: string[];
  pdt_mode?: string;
}

interface WorkerCreate {
  name: string;
  tailscale_ip: string;
  max_algorithms?: number;
}

interface InstanceCreate {
  account_id: string;
  worker_id: string;
  config_values?: Record<string, unknown>;
}

interface EventParams {
  event_type?: string;
  severity?: string;
  limit?: number;
  offset?: number;
}

// Re-export types used in api object
import type {
  Account,
  Algorithm,
  AlgorithmInstance,
  Worker,
  SystemEvent,
  PaginatedResponse,
  RepoInfo,
  SettingsStatus,
} from "../types";
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx tsc --noEmit`
Expected: No type errors

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/types/index.ts dashboard/src/api/client.ts
git commit -m "feat(dashboard): add TypeScript types and API client"
```

---

### Task 3: TanStack Query Hooks

**Files:**
- Create: `dashboard/src/api/hooks.ts`

- [ ] **Step 1: Create query hooks**

```typescript
// dashboard/src/api/hooks.ts
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";

// Accounts
export function useAccounts() {
  return useQuery({ queryKey: ["accounts"], queryFn: api.listAccounts });
}

export function useAccount(id: string) {
  return useQuery({
    queryKey: ["accounts", id],
    queryFn: () => api.getAccount(id),
    enabled: !!id,
  });
}

export function useCreateAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.createAccount,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts"] }),
  });
}

export function useDeleteAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.deleteAccount,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts"] }),
  });
}

// Workers
export function useWorkers() {
  return useQuery({ queryKey: ["workers"], queryFn: api.listWorkers });
}

export function useWorker(id: string) {
  return useQuery({
    queryKey: ["workers", id],
    queryFn: () => api.getWorker(id),
    enabled: !!id,
  });
}

export function useCreateWorker() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.createWorker,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["workers"] }),
  });
}

export function useDeleteWorker() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.deleteWorker,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["workers"] }),
  });
}

// Algorithms
export function useAlgorithms() {
  return useQuery({ queryKey: ["algorithms"], queryFn: api.listAlgorithms });
}

export function useAlgorithm(id: string) {
  return useQuery({
    queryKey: ["algorithms", id],
    queryFn: () => api.getAlgorithm(id),
    enabled: !!id,
  });
}

export function useInstances(algoId: string) {
  return useQuery({
    queryKey: ["algorithms", algoId, "instances"],
    queryFn: () => api.listInstances(algoId),
    enabled: !!algoId,
  });
}

export function useInstance(id: string) {
  return useQuery({
    queryKey: ["instances", id],
    queryFn: () => api.getInstance(id),
    enabled: !!id,
  });
}

export function useInstallAlgorithm() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: api.installAlgorithm,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["algorithms"] }),
  });
}

// Events
export function useEvents(params?: { event_type?: string; severity?: string; limit?: number; offset?: number }) {
  return useQuery({
    queryKey: ["events", params],
    queryFn: () => api.listEvents(params),
  });
}

// Settings
export function useSettings() {
  return useQuery({ queryKey: ["settings"], queryFn: api.getSettings });
}

// GitHub repos
export function useGithubRepos() {
  return useQuery({
    queryKey: ["github", "repos"],
    queryFn: api.listRepos,
    enabled: false,
  });
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx tsc --noEmit`
Expected: No type errors

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/api/hooks.ts
git commit -m "feat(dashboard): add TanStack Query hooks for all API endpoints"
```

---

### Task 4: WebSocket Manager + UI Store

**Files:**
- Create: `dashboard/src/api/websocket.ts`
- Create: `dashboard/src/stores/ui.ts`

- [ ] **Step 1: Create WebSocket manager**

```typescript
// dashboard/src/api/websocket.ts
type MessageHandler = (data: Record<string, unknown>) => void;

export class WebSocketManager {
  private ws: WebSocket | null = null;
  private handlers: Map<string, Set<MessageHandler>> = new Map();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private url: string;

  constructor(url: string = `ws://${window.location.host}/ws/dashboard`) {
    this.url = url;
  }

  connect(): void {
    this.ws = new WebSocket(this.url);

    this.ws.onopen = () => {
      console.log("WebSocket connected");
      this.ws?.send(JSON.stringify({ type: "ping" }));
    };

    this.ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      const type = data.type as string;
      this.handlers.get(type)?.forEach((handler) => handler(data));
      this.handlers.get("*")?.forEach((handler) => handler(data));
    };

    this.ws.onclose = () => {
      console.log("WebSocket disconnected, reconnecting in 3s...");
      this.reconnectTimer = setTimeout(() => this.connect(), 3000);
    };
  }

  disconnect(): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.ws?.close();
    this.ws = null;
  }

  subscribe(eventType: string, handler: MessageHandler): () => void {
    if (!this.handlers.has(eventType)) {
      this.handlers.set(eventType, new Set());
    }
    this.handlers.get(eventType)!.add(handler);
    return () => this.handlers.get(eventType)?.delete(handler);
  }

  send(data: Record<string, unknown>): void {
    this.ws?.send(JSON.stringify(data));
  }
}

export const wsManager = new WebSocketManager();
```

- [ ] **Step 2: Create UI store**

```typescript
// dashboard/src/stores/ui.ts
import { create } from "zustand";

interface UIState {
  sidebarOpen: boolean;
  toggleSidebar: () => void;
  activeAlerts: Alert[];
  addAlert: (alert: Alert) => void;
  dismissAlert: (id: string) => void;
}

export interface Alert {
  id: string;
  type: "info" | "warning" | "error";
  message: string;
  timestamp: string;
}

export const useUIStore = create<UIState>((set) => ({
  sidebarOpen: true,
  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
  activeAlerts: [],
  addAlert: (alert) =>
    set((state) => ({ activeAlerts: [alert, ...state.activeAlerts].slice(0, 20) })),
  dismissAlert: (id) =>
    set((state) => ({
      activeAlerts: state.activeAlerts.filter((a) => a.id !== id),
    })),
}));
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx tsc --noEmit`
Expected: No type errors

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/api/websocket.ts dashboard/src/stores/ui.ts
git commit -m "feat(dashboard): add WebSocket manager and UI state store"
```

---

### Task 5: Shared Components

**Files:**
- Create: `dashboard/src/components/Layout.tsx`
- Create: `dashboard/src/components/StatusBadge.tsx`
- Create: `dashboard/src/components/MetricsCard.tsx`
- Create: `dashboard/src/components/ConfirmDialog.tsx`

- [ ] **Step 1: Create Layout**

```tsx
// dashboard/src/components/Layout.tsx
import { Link, useLocation } from "react-router-dom";
import {
  LayoutDashboard,
  Wallet,
  Bot,
  Server,
  Database,
  GitCompare,
  Bell,
  Settings,
  Menu,
} from "lucide-react";
import { useUIStore } from "../stores/ui";
import { clsx } from "clsx";

const navItems = [
  { path: "/", label: "Overview", icon: LayoutDashboard },
  { path: "/accounts", label: "Accounts", icon: Wallet },
  { path: "/algorithms", label: "Algorithms", icon: Bot },
  { path: "/workers", label: "Workers", icon: Server },
  { path: "/data", label: "Data", icon: Database },
  { path: "/backtests", label: "Backtests", icon: GitCompare },
  { path: "/notifications", label: "Notifications", icon: Bell },
  { path: "/settings", label: "Settings", icon: Settings },
];

export function Layout({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const { sidebarOpen, toggleSidebar } = useUIStore();

  return (
    <div className="flex h-screen">
      <aside
        className={clsx(
          "bg-gray-900 border-r border-gray-800 flex flex-col transition-all",
          sidebarOpen ? "w-56" : "w-16"
        )}
      >
        <div className="p-4 flex items-center gap-2 border-b border-gray-800">
          <button onClick={toggleSidebar} className="p-1 hover:bg-gray-800 rounded">
            <Menu className="w-5 h-5" />
          </button>
          {sidebarOpen && (
            <span className="font-semibold text-sm">QuiltTrader</span>
          )}
        </div>
        <nav className="flex-1 py-2">
          {navItems.map(({ path, label, icon: Icon }) => (
            <Link
              key={path}
              to={path}
              className={clsx(
                "flex items-center gap-3 px-4 py-2 text-sm hover:bg-gray-800 transition-colors",
                location.pathname === path
                  ? "text-blue-400 bg-gray-800/50"
                  : "text-gray-400"
              )}
            >
              <Icon className="w-4 h-4 flex-shrink-0" />
              {sidebarOpen && <span>{label}</span>}
            </Link>
          ))}
        </nav>
      </aside>
      <main className="flex-1 overflow-auto p-6">{children}</main>
    </div>
  );
}
```

- [ ] **Step 2: Create StatusBadge**

```tsx
// dashboard/src/components/StatusBadge.tsx
import { clsx } from "clsx";

const statusColors: Record<string, string> = {
  running: "bg-green-500/20 text-green-400",
  online: "bg-green-500/20 text-green-400",
  stopped: "bg-gray-500/20 text-gray-400",
  offline: "bg-gray-500/20 text-gray-400",
  error: "bg-red-500/20 text-red-400",
  disconnected: "bg-yellow-500/20 text-yellow-400",
  installed: "bg-blue-500/20 text-blue-400",
  updating: "bg-yellow-500/20 text-yellow-400",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={clsx(
        "inline-flex items-center px-2 py-0.5 rounded text-xs font-medium",
        statusColors[status] ?? "bg-gray-500/20 text-gray-400"
      )}
    >
      {status}
    </span>
  );
}
```

- [ ] **Step 3: Create MetricsCard**

```tsx
// dashboard/src/components/MetricsCard.tsx
import { clsx } from "clsx";

interface MetricsCardProps {
  label: string;
  value: string | number;
  change?: number;
  prefix?: string;
  suffix?: string;
}

export function MetricsCard({ label, value, change, prefix = "", suffix = "" }: MetricsCardProps) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
      <div className="text-xs text-gray-500 uppercase tracking-wide">{label}</div>
      <div className="mt-1 text-2xl font-semibold">
        {prefix}{typeof value === "number" ? value.toLocaleString() : value}{suffix}
      </div>
      {change !== undefined && (
        <div
          className={clsx(
            "mt-1 text-xs",
            change >= 0 ? "text-profit" : "text-loss"
          )}
        >
          {change >= 0 ? "+" : ""}
          {change.toFixed(2)}%
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Create ConfirmDialog**

```tsx
// dashboard/src/components/ConfirmDialog.tsx
interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Confirm",
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="fixed inset-0 bg-black/60" onClick={onCancel} />
      <div className="relative bg-gray-900 border border-gray-700 rounded-lg p-6 max-w-md w-full mx-4">
        <h3 className="text-lg font-semibold">{title}</h3>
        <p className="mt-2 text-sm text-gray-400">{message}</p>
        <div className="mt-4 flex justify-end gap-3">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm rounded bg-gray-800 hover:bg-gray-700 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className="px-4 py-2 text-sm rounded bg-red-600 hover:bg-red-500 transition-colors"
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Commit**

```bash
git add dashboard/src/components/
git commit -m "feat(dashboard): add Layout, StatusBadge, MetricsCard, ConfirmDialog components"
```

---

### Task 6: App Shell + Router + Page Stubs

**Files:**
- Create: `dashboard/src/App.tsx`
- Create: all page files as stubs

- [ ] **Step 1: Create App.tsx with router**

```tsx
// dashboard/src/App.tsx
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Layout } from "./components/Layout";
import { Overview } from "./pages/Overview";
import { Accounts } from "./pages/Accounts";
import { AccountDetail } from "./pages/AccountDetail";
import { Algorithms } from "./pages/Algorithms";
import { AlgorithmDetail } from "./pages/AlgorithmDetail";
import { InstanceDetail } from "./pages/InstanceDetail";
import { RunDetail } from "./pages/RunDetail";
import { Workers } from "./pages/Workers";
import { Data } from "./pages/Data";
import { Backtests } from "./pages/Backtests";
import { BacktestDetail } from "./pages/BacktestDetail";
import { Notifications } from "./pages/Notifications";
import { SettingsPage } from "./pages/Settings";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
      staleTime: 10_000,
    },
  },
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Layout>
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/accounts" element={<Accounts />} />
            <Route path="/accounts/:id" element={<AccountDetail />} />
            <Route path="/algorithms" element={<Algorithms />} />
            <Route path="/algorithms/:id" element={<AlgorithmDetail />} />
            <Route path="/instances/:id" element={<InstanceDetail />} />
            <Route path="/runs/:id" element={<RunDetail />} />
            <Route path="/workers" element={<Workers />} />
            <Route path="/data" element={<Data />} />
            <Route path="/backtests" element={<Backtests />} />
            <Route path="/backtests/:id" element={<BacktestDetail />} />
            <Route path="/notifications" element={<Notifications />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Routes>
        </Layout>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
```

- [ ] **Step 2: Create page stubs**

Create each page as a minimal component with a heading. Each will be fleshed out in later steps. Example pattern for all pages:

```tsx
// dashboard/src/pages/Overview.tsx
import { useAccounts } from "../api/hooks";
import { useWorkers } from "../api/hooks";
import { StatusBadge } from "../components/StatusBadge";
import { MetricsCard } from "../components/MetricsCard";

export function Overview() {
  const { data: accounts, isLoading: accountsLoading } = useAccounts();
  const { data: workers, isLoading: workersLoading } = useWorkers();

  if (accountsLoading || workersLoading) {
    return <div className="text-gray-400">Loading...</div>;
  }

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Overview</h1>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
        <MetricsCard label="Accounts" value={accounts?.length ?? 0} />
        <MetricsCard label="Workers" value={workers?.length ?? 0} />
        <MetricsCard label="Running Algorithms" value={0} />
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <h2 className="text-sm font-semibold text-gray-400 uppercase mb-3">Workers</h2>
          {workers?.map((w) => (
            <div key={w.id} className="flex items-center justify-between py-2">
              <span>{w.name}</span>
              <StatusBadge status={w.status} />
            </div>
          ))}
          {workers?.length === 0 && <p className="text-gray-500 text-sm">No workers registered</p>}
        </div>
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <h2 className="text-sm font-semibold text-gray-400 uppercase mb-3">Accounts</h2>
          {accounts?.map((a) => (
            <div key={a.id} className="flex items-center justify-between py-2">
              <span>{a.name}</span>
              <span className="text-xs text-gray-500">{a.broker_type}</span>
            </div>
          ))}
          {accounts?.length === 0 && <p className="text-gray-500 text-sm">No accounts configured</p>}
        </div>
      </div>
    </div>
  );
}
```

```tsx
// dashboard/src/pages/Accounts.tsx
import { Link } from "react-router-dom";
import { useAccounts } from "../api/hooks";
import { StatusBadge } from "../components/StatusBadge";

export function Accounts() {
  const { data: accounts, isLoading } = useAccounts();
  if (isLoading) return <div className="text-gray-400">Loading...</div>;
  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Accounts</h1>
        <button className="px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded text-sm">
          Add Account
        </button>
      </div>
      <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-gray-800 text-left text-xs text-gray-500 uppercase">
              <th className="px-4 py-3">Name</th>
              <th className="px-4 py-3">Broker</th>
              <th className="px-4 py-3">Asset Types</th>
              <th className="px-4 py-3">PDT Mode</th>
              <th className="px-4 py-3">Status</th>
            </tr>
          </thead>
          <tbody>
            {accounts?.map((a) => (
              <tr key={a.id} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                <td className="px-4 py-3">
                  <Link to={`/accounts/${a.id}`} className="text-blue-400 hover:underline">{a.name}</Link>
                </td>
                <td className="px-4 py-3 text-sm">{a.broker_type}</td>
                <td className="px-4 py-3 text-sm text-gray-400">{a.supported_asset_types.join(", ")}</td>
                <td className="px-4 py-3"><StatusBadge status={a.pdt_mode} /></td>
                <td className="px-4 py-3">
                  {a.locked_by ? <StatusBadge status="running" /> : <StatusBadge status="available" />}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
```

Create the remaining page stubs similarly — each with a title and basic data fetching where applicable. For brevity, each stub follows this pattern:

```tsx
// dashboard/src/pages/AccountDetail.tsx
import { useParams } from "react-router-dom";
export function AccountDetail() {
  const { id } = useParams<{ id: string }>();
  return <div><h1 className="text-2xl font-bold mb-6">Account Detail</h1><p className="text-gray-400">Account ID: {id}</p></div>;
}

// dashboard/src/pages/Algorithms.tsx
import { useAlgorithms } from "../api/hooks";
export function Algorithms() {
  const { data, isLoading } = useAlgorithms();
  if (isLoading) return <div className="text-gray-400">Loading...</div>;
  return <div><h1 className="text-2xl font-bold mb-6">Algorithms ({data?.length ?? 0})</h1></div>;
}

// dashboard/src/pages/AlgorithmDetail.tsx
import { useParams } from "react-router-dom";
export function AlgorithmDetail() {
  const { id } = useParams<{ id: string }>();
  return <div><h1 className="text-2xl font-bold mb-6">Algorithm Detail</h1><p className="text-gray-400">Algorithm ID: {id}</p></div>;
}

// dashboard/src/pages/InstanceDetail.tsx
import { useParams } from "react-router-dom";
export function InstanceDetail() {
  const { id } = useParams<{ id: string }>();
  return <div><h1 className="text-2xl font-bold mb-6">Instance Detail</h1><p className="text-gray-400">Instance ID: {id}</p></div>;
}

// dashboard/src/pages/RunDetail.tsx
import { useParams } from "react-router-dom";
export function RunDetail() {
  const { id } = useParams<{ id: string }>();
  return <div><h1 className="text-2xl font-bold mb-6">Run Detail</h1><p className="text-gray-400">Run ID: {id}</p></div>;
}

// dashboard/src/pages/Workers.tsx
import { useWorkers } from "../api/hooks";
export function Workers() {
  const { data, isLoading } = useWorkers();
  if (isLoading) return <div className="text-gray-400">Loading...</div>;
  return <div><h1 className="text-2xl font-bold mb-6">Workers ({data?.length ?? 0})</h1></div>;
}

// dashboard/src/pages/Data.tsx
export function Data() {
  return <div><h1 className="text-2xl font-bold mb-6">Data Management</h1></div>;
}

// dashboard/src/pages/Backtests.tsx
export function Backtests() {
  return <div><h1 className="text-2xl font-bold mb-6">Backtest Comparisons</h1></div>;
}

// dashboard/src/pages/BacktestDetail.tsx
import { useParams } from "react-router-dom";
export function BacktestDetail() {
  const { id } = useParams<{ id: string }>();
  return <div><h1 className="text-2xl font-bold mb-6">Backtest Detail</h1><p className="text-gray-400">Comparison ID: {id}</p></div>;
}

// dashboard/src/pages/Notifications.tsx
export function Notifications() {
  return <div><h1 className="text-2xl font-bold mb-6">Notification Settings</h1></div>;
}

// dashboard/src/pages/Settings.tsx
import { useSettings } from "../api/hooks";
export function SettingsPage() {
  const { data, isLoading } = useSettings();
  if (isLoading) return <div className="text-gray-400">Loading...</div>;
  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Settings</h1>
      <div className="space-y-4">
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <h2 className="text-sm font-semibold text-gray-400 uppercase mb-2">Connections</h2>
          <p className="text-sm">GitHub PAT: {data?.github_pat_set ? "Configured" : "Not set"}</p>
          <p className="text-sm">Discord Bot: {data?.discord_bot_token_set ? "Configured" : "Not set"}</p>
          <p className="text-sm">Polygon: {data?.polygon_api_key_set ? "Configured" : "Not set"}</p>
          <p className="text-sm">Theta Data: {data?.theta_data_set ? "Configured" : "Not set"}</p>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Verify build**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx tsc --noEmit && npx vite build`
Expected: Build succeeds

- [ ] **Step 4: Commit**

```bash
git add dashboard/src/
git commit -m "feat(dashboard): add app shell, router, and all page stubs"
```

---

### Task 7: Equity Curve Chart Component

**Files:**
- Create: `dashboard/src/components/EquityCurve.tsx`

- [ ] **Step 1: Create the Lightweight Charts wrapper**

```tsx
// dashboard/src/components/EquityCurve.tsx
import { useEffect, useRef } from "react";
import { createChart, type IChartApi, type ISeriesApi } from "lightweight-charts";

interface EquityCurveProps {
  data: { timestamp: string; equity: number }[];
  height?: number;
}

export function EquityCurve({ data, height = 300 }: EquityCurveProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Area"> | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      height,
      layout: {
        background: { color: "transparent" },
        textColor: "#9ca3af",
      },
      grid: {
        vertLines: { color: "#1f2937" },
        horzLines: { color: "#1f2937" },
      },
      timeScale: {
        borderColor: "#374151",
        timeVisible: true,
      },
      rightPriceScale: {
        borderColor: "#374151",
      },
    });

    const series = chart.addAreaSeries({
      lineColor: "#3b82f6",
      topColor: "rgba(59, 130, 246, 0.3)",
      bottomColor: "rgba(59, 130, 246, 0.0)",
      lineWidth: 2,
    });

    chartRef.current = chart;
    seriesRef.current = series;

    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        chart.applyOptions({ width: entry.contentRect.width });
      }
    });
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
    };
  }, [height]);

  useEffect(() => {
    if (!seriesRef.current || data.length === 0) return;
    const chartData = data.map((d) => ({
      time: Math.floor(new Date(d.timestamp).getTime() / 1000) as any,
      value: d.equity,
    }));
    seriesRef.current.setData(chartData);
    chartRef.current?.timeScale().fitContent();
  }, [data]);

  return <div ref={containerRef} className="w-full" />;
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx tsc --noEmit`
Expected: No type errors

- [ ] **Step 3: Commit**

```bash
git add dashboard/src/components/EquityCurve.tsx
git commit -m "feat(dashboard): add EquityCurve component with Lightweight Charts"
```

---

### Task 8: Wire Dashboard Static Serving in Coordinator

**Files:**
- Modify: `coordinator/main.py`

- [ ] **Step 1: Add static file serving for production builds**

Add to `coordinator/main.py` after all router inclusions, before `return app`:

```python
    import os
    dashboard_dir = os.path.join(os.path.dirname(__file__), "..", "dashboard", "dist")
    if os.path.isdir(dashboard_dir):
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=dashboard_dir, html=True), name="dashboard")
```

- [ ] **Step 2: Verify the app still starts when dashboard/dist doesn't exist**

Run: `cd /home/jkern/dev/quilt-trader && python -c "from coordinator.main import create_app; app = create_app(); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add coordinator/main.py
git commit -m "feat(coordinator): serve dashboard static files in production"
```

---

### Task 9: Final Verification

**Files:** None (verification only)

- [ ] **Step 1: Build the dashboard**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npm run build`
Expected: Build completes, `dist/` directory created

- [ ] **Step 2: Start dev server and verify in browser**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npm run dev`
Expected: Dev server starts on port 3000. Open browser to http://localhost:3000 and verify:
- Sidebar renders with all navigation items
- Overview page shows metrics cards
- Navigation between pages works
- No console errors

- [ ] **Step 3: TypeScript check**

Run: `cd /home/jkern/dev/quilt-trader/dashboard && npx tsc --noEmit`
Expected: No type errors

- [ ] **Step 4: Run all backend tests to ensure no regressions**

Run: `cd /home/jkern/dev/quilt-trader && python -m pytest tests/ -v --tb=short`
Expected: All tests pass
