import type {
  Account,
  Algorithm,
  AlgorithmGitStatus,
  AlgorithmInstance,
  AlgorithmRun,
  InstalledAlgorithmResponse,
  Worker,
  SystemEvent,
  PaginatedResponse,
  RepoInfo,
  SettingsStatus,
  HealthResponse,
  CashFlow,
  BacktestComparison,
  MarketDataDownload,
  AvailableMarketData,
  MarketDataResponse,
  PortfolioEquityResponse,
  PortfolioKpis,
  AllocationResponse,
  OpenPositionRow,
  TradeRow,
  AlertItem,
  AccountSnapshotLatestItem,
} from "../types";

// ─── Request body types ────────────────────────────────────────────────────────

export interface AccountCreate {
  name: string;
  broker_type: string;
  environment?: "paper" | "live";
  credentials: Record<string, unknown>;
  supported_asset_types: string[];
  options_level?: number;
  account_features?: string[];
  pdt_mode?: string;
}

export interface AccountUpdate {
  name?: string;
  environment?: "paper" | "live";
  credentials?: Record<string, unknown>;
  supported_asset_types?: string[];
  options_level?: number;
  account_features?: string[];
  pdt_mode?: string;
}

export interface TestConnectionRequest {
  broker_type: string;
  environment: "paper" | "live";
  credentials: Record<string, unknown>;
}

export interface TestConnectionResponse {
  ok: boolean;
  error?: string;
  info?: {
    cash: number | null;
    portfolio_value: number | null;
    buying_power: number | null;
    currency: string | null;
  };
}

export interface BrokerPosition {
  symbol: string;
  quantity: number;
  side: string;
  avg_price: number;
  current_price: number;
  unrealized_pnl: number;
  market_value: number;
}

export interface BrokerInfo {
  account_info: {
    cash: number;
    portfolio_value: number;
    buying_power: number;
    equity?: number;
    currency?: string;
  };
  positions: BrokerPosition[];
}

export interface SyncResult {
  ok: boolean;
  since: string;
  trades_inserted: number;
  cash_flows_inserted: number;
  total_fetched: number;
  snapshot: {
    total_value: number;
    cash: number;
    positions_value: number;
  };
  positions_count: number;
}

export interface EquityCurvePoint {
  timestamp: string;
  value: number;
  source: "snapshot" | "estimated" | "live";
}

export interface ScraperRecord {
  name: string;
  schedule: string;
  jitter_seconds: number | null;
  next_run_at: string | null;
  version: string | null;
  description: string | null;
  config_overrides: string[];
  last_status: string | null;
  last_run_at: string | null;
  data_url: string;
  last_error: string | null;
}

export interface ScraperInstall {
  repo_url: string;
  name?: string;
}

export interface DataSourceRow {
  id: string;
  type: string;
  source: string;
  name: string;
  description: string | null;
  file_path: string | null;
  last_updated: string | null;
  metadata: Record<string, unknown> | null;
}

export interface CustomDatasetResponse {
  data: Record<string, unknown>[];
}

export interface WorkerCreate {
  name: string;
  tailscale_ip: string;
  max_algorithms?: number;
}

export interface WorkerUpdate {
  name?: string;
  tailscale_ip?: string;
  max_algorithms?: number;
}

export interface InstanceCreate {
  account_id: string;
  worker_id: string;
  config_values?: Record<string, unknown>;
}

export interface InstanceUpdate {
  config_values?: Record<string, unknown>;
  status?: string;
}

export interface CashFlowCreate {
  type: string;
  amount: number;
  notes?: string;
}

export interface DownloadCreate {
  symbols: string[];
  date_range_start: string;
  date_range_end: string;
  provider: string;
  data_type: string;
  timeframe: string;
}

export interface EventParams {
  event_type?: string;
  severity?: string;
  source_type?: string;
  limit?: number;
  offset?: number;
}

// ─── Generic request helper ────────────────────────────────────────────────────

async function request<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const res = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...options.headers,
    },
    ...options,
  });

  if (res.status === 204) {
    return undefined as unknown as T;
  }

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (body.detail) detail = body.detail;
    } catch {
      // ignore
    }
    throw new Error(`${res.status}: ${detail}`);
  }

  const contentType = res.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) {
    return res.json() as Promise<T>;
  }

  return res.text() as unknown as Promise<T>;
}

// ─── API object ────────────────────────────────────────────────────────────────

export const api = {
  // Health
  health(): Promise<HealthResponse> {
    return request<HealthResponse>("/api/health");
  },

  // Accounts
  listAccounts(): Promise<Account[]> {
    return request<Account[]>("/api/accounts");
  },
  getAccount(id: string): Promise<Account> {
    return request<Account>(`/api/accounts/${id}`);
  },
  createAccount(body: AccountCreate): Promise<Account> {
    return request<Account>("/api/accounts", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
  updateAccount(id: string, body: AccountUpdate): Promise<Account> {
    return request<Account>(`/api/accounts/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  },
  deleteAccount(id: string): Promise<void> {
    return request<void>(`/api/accounts/${id}`, { method: "DELETE" });
  },
  testAccountConnection(body: TestConnectionRequest): Promise<TestConnectionResponse> {
    return request<TestConnectionResponse>("/api/accounts/test-connection", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
  getBrokerInfo(id: string): Promise<BrokerInfo> {
    return request<BrokerInfo>(`/api/accounts/${id}/broker-info`);
  },
  syncAccount(id: string, since?: string): Promise<SyncResult> {
    return request<SyncResult>(`/api/accounts/${id}/sync`, {
      method: "POST",
      body: JSON.stringify(since ? { since } : {}),
    });
  },
  getEquityCurve(id: string, since?: string): Promise<{ items: EquityCurvePoint[] }> {
    const qs = since ? `?since=${encodeURIComponent(since)}` : "";
    return request<{ items: EquityCurvePoint[] }>(`/api/accounts/${id}/equity-curve${qs}`);
  },

  // Workers
  listWorkers(): Promise<Worker[]> {
    return request<Worker[]>("/api/workers");
  },
  getWorker(id: string): Promise<Worker> {
    return request<Worker>(`/api/workers/${id}`);
  },
  createWorker(body: WorkerCreate): Promise<Worker> {
    return request<Worker>("/api/workers", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
  updateWorker(id: string, body: WorkerUpdate): Promise<Worker> {
    return request<Worker>(`/api/workers/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  },
  deleteWorker(id: string): Promise<void> {
    return request<void>(`/api/workers/${id}`, { method: "DELETE" });
  },
  getWorkerInstallCommand(id: string): Promise<string> {
    return request<string>(`/api/workers/${id}/install-command`);
  },
  regenerateWorkerInstallToken(id: string): Promise<Worker> {
    return request<Worker>(`/api/workers/${id}/regenerate-token`, { method: "POST" });
  },

  // Algorithms
  listAlgorithms(): Promise<Algorithm[]> {
    return request<Algorithm[]>("/api/algorithms");
  },
  getAlgorithm(id: string): Promise<Algorithm> {
    return request<Algorithm>(`/api/algorithms/${id}`);
  },
  deleteAlgorithm(id: string): Promise<void> {
    return request<void>(`/api/algorithms/${id}`, { method: "DELETE" });
  },
  getAlgorithmGitStatus(id: string): Promise<AlgorithmGitStatus> {
    return request<AlgorithmGitStatus>(`/api/algorithms/${id}/git-status`);
  },
  updateAlgorithm(id: string): Promise<Algorithm> {
    return request<Algorithm>(`/api/algorithms/${id}/update`, { method: "POST" });
  },

  // Instances
  listInstances(algorithmId: string): Promise<AlgorithmInstance[]> {
    return request<AlgorithmInstance[]>(
      `/api/algorithms/${algorithmId}/instances`
    );
  },
  createInstance(
    algorithmId: string,
    body: InstanceCreate
  ): Promise<AlgorithmInstance> {
    return request<AlgorithmInstance>(
      `/api/algorithms/${algorithmId}/instances`,
      {
        method: "POST",
        body: JSON.stringify(body),
      }
    );
  },
  getInstance(instanceId: string): Promise<AlgorithmInstance> {
    return request<AlgorithmInstance>(`/api/instances/${instanceId}`);
  },
  listAllInstances(): Promise<AlgorithmInstance[]> {
    return request<AlgorithmInstance[]>("/api/instances");
  },
  updateInstance(id: string, body: InstanceUpdate): Promise<AlgorithmInstance> {
    return request<AlgorithmInstance>(`/api/instances/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  },
  deleteInstance(id: string): Promise<void> {
    return request<void>(`/api/instances/${id}`, { method: "DELETE" });
  },

  // Runs
  listRuns(instanceId: string): Promise<AlgorithmRun[]> {
    return request<AlgorithmRun[]>(`/api/instances/${instanceId}/runs`);
  },
  getRun(runId: string): Promise<AlgorithmRun> {
    return request<AlgorithmRun>(`/api/runs/${runId}`);
  },

  // Cash Flows
  listCashFlows(accountId: string): Promise<CashFlow[]> {
    return request<CashFlow[]>(`/api/accounts/${accountId}/cash-flows`);
  },
  createCashFlow(accountId: string, body: CashFlowCreate): Promise<CashFlow> {
    return request<CashFlow>(`/api/accounts/${accountId}/cash-flows`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  // Backtests
  listBacktests(): Promise<BacktestComparison[]> {
    return request<BacktestComparison[]>("/api/backtests");
  },
  getBacktest(id: string): Promise<BacktestComparison> {
    return request<BacktestComparison>(`/api/backtests/${id}`);
  },

  // Data
  listAvailableData(): Promise<AvailableMarketData[]> {
    return request<AvailableMarketData[]>("/api/data/available");
  },
  listScrapers(): Promise<ScraperRecord[]> {
    return request<ScraperRecord[]>("/api/scrapers");
  },
  installScraper(body: ScraperInstall): Promise<ScraperRecord> {
    return request<ScraperRecord>("/api/scrapers", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
  deleteScraper(name: string): Promise<void> {
    return request<void>(`/api/scrapers/${encodeURIComponent(name)}`, { method: "DELETE" });
  },
  runScraper(name: string): Promise<{ success: boolean; error: string | null; record: ScraperRecord }> {
    return request(`/api/scrapers/${encodeURIComponent(name)}/run`, { method: "POST" });
  },
  listDataSources(type?: string): Promise<DataSourceRow[]> {
    const qs = type ? `?type=${encodeURIComponent(type)}` : "";
    return request<DataSourceRow[]>(`/api/data/sources${qs}`);
  },
  getCustomData(name: string): Promise<CustomDatasetResponse> {
    return request<CustomDatasetResponse>(`/api/data/custom/${encodeURIComponent(name)}`);
  },
  listDownloads(): Promise<MarketDataDownload[]> {
    return request<MarketDataDownload[]>("/api/data/downloads");
  },
  getDownload(id: string): Promise<MarketDataDownload> {
    return request<MarketDataDownload>(`/api/data/downloads/${id}`);
  },
  createDownload(body: DownloadCreate): Promise<MarketDataDownload> {
    return request<MarketDataDownload>("/api/data/downloads", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
  cancelDownload(id: string): Promise<MarketDataDownload> {
    return request<MarketDataDownload>(`/api/data/downloads/${id}/cancel`, {
      method: "POST",
    });
  },
  deleteDownload(id: string): Promise<void> {
    return request<void>(`/api/data/downloads/${id}`, { method: "DELETE" });
  },
  clearDownloads(status?: string): Promise<{ deleted: number }> {
    const qs = status ? `?status=${encodeURIComponent(status)}` : "";
    return request<{ deleted: number }>(`/api/data/downloads${qs}`, { method: "DELETE" });
  },
  getMarketData(provider: string, symbol: string, timeframe: string): Promise<MarketDataResponse> {
    return request<MarketDataResponse>(
      `/api/data/market/${encodeURIComponent(symbol)}?provider=${encodeURIComponent(provider)}&timeframe=${encodeURIComponent(timeframe)}`
    );
  },

  // Events
  listEvents(params: EventParams = {}): Promise<PaginatedResponse<SystemEvent>> {
    const qs = new URLSearchParams();
    if (params.event_type) qs.set("event_type", params.event_type);
    if (params.severity) qs.set("severity", params.severity);
    if (params.source_type) qs.set("source_type", params.source_type);
    if (params.limit !== undefined) qs.set("limit", String(params.limit));
    if (params.offset !== undefined) qs.set("offset", String(params.offset));
    const query = qs.toString();
    return request<PaginatedResponse<SystemEvent>>(
      `/api/events${query ? `?${query}` : ""}`
    );
  },

  // Settings
  getSettings(): Promise<SettingsStatus> {
    return request<SettingsStatus>("/api/settings");
  },
  setGithubPat(value: string): Promise<SettingsStatus> {
    return request<SettingsStatus>("/api/settings/github-pat", {
      method: "PUT",
      body: JSON.stringify({ value }),
    });
  },
  setDiscordToken(value: string): Promise<SettingsStatus> {
    return request<SettingsStatus>("/api/settings/discord-token", {
      method: "PUT",
      body: JSON.stringify({ value }),
    });
  },
  setPolygonKey(value: string): Promise<SettingsStatus> {
    return request<SettingsStatus>("/api/settings/polygon-key", {
      method: "PUT",
      body: JSON.stringify({ value }),
    });
  },
  setThetaData(
    username: string,
    password: string
  ): Promise<SettingsStatus> {
    return request<SettingsStatus>("/api/settings/theta-data", {
      method: "PUT",
      body: JSON.stringify({ username, password }),
    });
  },
  deleteGithubPat(): Promise<SettingsStatus> {
    return request<SettingsStatus>("/api/settings/github-pat", {
      method: "DELETE",
    });
  },
  deleteDiscordToken(): Promise<SettingsStatus> {
    return request<SettingsStatus>("/api/settings/discord-token", {
      method: "DELETE",
    });
  },
  deletePolygonKey(): Promise<SettingsStatus> {
    return request<SettingsStatus>("/api/settings/polygon-key", {
      method: "DELETE",
    });
  },
  deleteThetaData(): Promise<SettingsStatus> {
    return request<SettingsStatus>("/api/settings/theta-data", {
      method: "DELETE",
    });
  },

  // GitHub
  listRepos(): Promise<RepoInfo[]> {
    return request<RepoInfo[]>("/api/github/repos");
  },
  installAlgorithm(
    full_name: string
  ): Promise<InstalledAlgorithmResponse> {
    return request<InstalledAlgorithmResponse>("/api/github/install", {
      method: "POST",
      body: JSON.stringify({ full_name }),
    });
  },

  // Portfolio
  portfolioEquity(range: "1d" | "1w" | "1m" | "all" = "1m"): Promise<PortfolioEquityResponse> {
    return request<PortfolioEquityResponse>(`/api/portfolio/equity?range=${range}`);
  },
  portfolioKpis(): Promise<PortfolioKpis> {
    return request<PortfolioKpis>("/api/portfolio/kpis");
  },
  portfolioAllocation(): Promise<AllocationResponse> {
    return request<AllocationResponse>("/api/portfolio/allocation");
  },

  // Positions
  listOpenPositions(limit = 10): Promise<{ items: OpenPositionRow[] }> {
    return request<{ items: OpenPositionRow[] }>(`/api/positions?status=open&limit=${limit}`);
  },

  // Trades
  listRecentTrades(limit = 10): Promise<{ items: TradeRow[] }> {
    return request<{ items: TradeRow[] }>(`/api/trades?limit=${limit}`);
  },
  listAccountTrades(accountId: string, limit = 100): Promise<{ items: TradeRow[] }> {
    return request<{ items: TradeRow[] }>(
      `/api/trades?limit=${limit}&account_id=${encodeURIComponent(accountId)}`
    );
  },

  // Alerts
  listAlerts(limit = 10): Promise<{ items: AlertItem[] }> {
    return request<{ items: AlertItem[] }>(`/api/alerts?limit=${limit}`);
  },

  // Account snapshots
  accountSnapshotsLatest(): Promise<{ items: AccountSnapshotLatestItem[] }> {
    return request<{ items: AccountSnapshotLatestItem[] }>("/api/accounts/snapshots/latest");
  },

  // ── U3: install algorithm from URL ──
  installAlgorithmFromUrl(repo_url: string): Promise<InstalledAlgorithmResponse> {
    return request<InstalledAlgorithmResponse>("/api/algorithms/install-from-url", {
      method: "POST",
      body: JSON.stringify({ repo_url }),
    });
  },

  // ── U2: open position ──
  openPosition(
    accountId: string,
    body: {
      legs: Array<{
        symbol: string;
        asset_type: string;
        side: "buy" | "sell";
        quantity: number;
        expiry?: string;
        strike?: number;
        right?: "call" | "put";
      }>;
      strategy_type?: string;
      order_type?: "market" | "limit";
      limit_price?: number;
    }
  ): Promise<{
    position_id: string | null;
    broker_order_id: string | null;
    legs: Array<{
      index: number;
      status: string;
      filled_price: number | null;
      fees: number | null;
      error: string | null;
      broker_order_id: string | null;
    }>;
    atomic: boolean;
    partial_fill: boolean;
  }> {
    return request(`/api/accounts/${accountId}/positions/open`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  // ── U1: broker asset-type catalog ──
  getBrokerAssetTypes(brokerType: string): Promise<{ asset_types: string[] }> {
    return request<{ asset_types: string[] }>(`/api/brokers/${brokerType}/asset-types`);
  },
};
