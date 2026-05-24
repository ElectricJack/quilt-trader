import type {
  Account,
  Algorithm,
  AlgorithmGitStatus,
  AlgorithmInstance,
  AlgorithmRun,
  Deployment,
  InstalledAlgorithmResponse,
  ParameterSet,
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
  BacktestReport,
  BacktestEquityWindow,
  ActivityRow,
  DeploymentReport,
  DeploymentTrade,
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
  show_in_overview?: boolean;
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
  asset_class: string;
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
  tailscale_ip?: string | null;
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
  parameter_set_id?: string;
}

export interface DeploymentUpdate {
  config_values?: Record<string, unknown>;
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

export interface CoverageRange {
  start: string;
  end: string;
}

export interface CoverageAsset {
  provider: string;
  symbol: string;
  ranges: CoverageRange[];
  timeframes_on_disk: string[];
  option_expirations?: string[];
}

export interface CoverageResponse {
  providers: Record<string, CoverageAsset[]>;
}

export interface FillGapsRequest {
  provider: string;
  symbol: string;
  start: string;
  end: string;
  timeframe?: string;
}

export interface FillGapsResponse {
  download_ids: string[];
  gap_count: number;
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
  triggerWorkerUpdate(id: string): Promise<{ status: string; worker_id: string }> {
    return request<{ status: string; worker_id: string }>(
      `/api/workers/${encodeURIComponent(id)}/update`,
      { method: "POST" },
    );
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

  // Parameter Sets
  listParameterSets(algorithmId: string): Promise<ParameterSet[]> {
    return request<ParameterSet[]>(
      `/api/algorithms/${algorithmId}/parameter-sets`
    );
  },
  createParameterSet(
    algorithmId: string,
    body: { name: string; config_values: Record<string, unknown> }
  ): Promise<ParameterSet> {
    return request<ParameterSet>(
      `/api/algorithms/${algorithmId}/parameter-sets`,
      { method: "POST", body: JSON.stringify(body) }
    );
  },
  updateParameterSet(
    algorithmId: string,
    setId: string,
    body: { name: string }
  ): Promise<ParameterSet> {
    return request<ParameterSet>(
      `/api/algorithms/${algorithmId}/parameter-sets/${setId}`,
      { method: "PATCH", body: JSON.stringify(body) }
    );
  },
  deleteParameterSet(algorithmId: string, setId: string): Promise<void> {
    return request<void>(
      `/api/algorithms/${algorithmId}/parameter-sets/${setId}`,
      { method: "DELETE" }
    );
  },
  exportParameterSets(algorithmId: string): Promise<Blob> {
    return fetch(`/api/algorithms/${algorithmId}/parameter-sets/export`).then(
      (r) => r.blob()
    );
  },
  importParameterSets(
    algorithmId: string,
    sets: Array<{ name: string; config_values: Record<string, unknown> }>
  ): Promise<{ imported: number; skipped: number }> {
    return request<{ imported: number; skipped: number }>(
      `/api/algorithms/${algorithmId}/parameter-sets/import`,
      { method: "POST", body: JSON.stringify({ sets }) }
    );
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
  listAllInstances(): Promise<AlgorithmInstance[]> {
    return request<AlgorithmInstance[]>("/api/instances");
  },

  // Deployments
  listDeployments(params?: { algorithm_id?: string; worker_id?: string; account_id?: string }): Promise<Deployment[]> {
    const qs = new URLSearchParams();
    if (params?.algorithm_id) qs.set("algorithm_id", params.algorithm_id);
    if (params?.worker_id) qs.set("worker_id", params.worker_id);
    if (params?.account_id) qs.set("account_id", params.account_id);
    const query = qs.toString();
    return request<Deployment[]>(`/api/deployments${query ? `?${query}` : ""}`);
  },
  getDeployment(id: string): Promise<Deployment> {
    return request<Deployment>(`/api/deployments/${id}`);
  },
  updateDeployment(id: string, body: DeploymentUpdate): Promise<{ ok: boolean }> {
    return request<{ ok: boolean }>(`/api/deployments/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  },
  deleteDeployment(id: string): Promise<void> {
    return request<void>(`/api/deployments/${id}`, { method: "DELETE" });
  },
  listDeploymentRuns(id: string): Promise<AlgorithmRun[]> {
    return request<AlgorithmRun[]>(`/api/deployments/${id}/runs`);
  },
  startDeployment(id: string): Promise<{ ok: boolean; active_run_id: string }> {
    return request<{ ok: boolean; active_run_id: string }>(`/api/deployments/${id}/start`, {
      method: "POST",
      body: JSON.stringify({}),
    });
  },
  stopDeployment(id: string): Promise<{ ok: boolean }> {
    return request<{ ok: boolean }>(`/api/deployments/${id}/stop`, {
      method: "POST",
      body: JSON.stringify({}),
    });
  },
  redeployDeployment(id: string): Promise<{
    id: string;
    status: string;
    commit_hash: string;
    commit_hash_short: string;
    was_running: boolean;
    restarted: boolean;
    active_run_id: string | null;
  }> {
    return request(`/api/deployments/${encodeURIComponent(id)}/redeploy`, {
      method: "POST",
    });
  },
  getDeploymentReport(id: string): Promise<DeploymentReport> {
    return request<DeploymentReport>(`/api/deployments/${id}/report`);
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
  retryDownload(downloadId: string): Promise<{
    original_download_id: string;
    new_download_ids: string[];
    new_download_count: number;
    skipped_symbols: string[];
    skipped_count: number;
    message: string;
  }> {
    return request(`/api/data/downloads/${encodeURIComponent(downloadId)}/retry`, {
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
  getCoverage(): Promise<CoverageResponse> {
    return request<CoverageResponse>("/api/data/coverage");
  },
  fillGaps(body: FillGapsRequest): Promise<FillGapsResponse> {
    return request<FillGapsResponse>("/api/data/fill-gaps", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
  deleteDatasets(body: { provider: string; symbol: string; timeframe: string }[]): Promise<{ deleted: number }> {
    return request<{ deleted: number }>("/api/data/delete-datasets", {
      method: "POST",
      body: JSON.stringify(body),
    });
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
  setCoordinatorIp(value: string): Promise<SettingsStatus> {
    return request<SettingsStatus>("/api/settings/coordinator-ip", {
      method: "PUT",
      body: JSON.stringify({ value }),
    });
  },
  deleteCoordinatorIp(): Promise<SettingsStatus> {
    return request<SettingsStatus>("/api/settings/coordinator-ip", {
      method: "DELETE",
    });
  },
  setTailscaleAuthkey(value: string): Promise<SettingsStatus> {
    return request<SettingsStatus>("/api/settings/tailscale-authkey", {
      method: "PUT",
      body: JSON.stringify({ value }),
    });
  },
  deleteTailscaleAuthkey(): Promise<SettingsStatus> {
    return request<SettingsStatus>("/api/settings/tailscale-authkey", {
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

  // ── U3: close position ──
  closePosition(
    accountId: string,
    body: {
      symbol: string;
      asset_type: string;
      side: "long" | "short";
      quantity: number;
    }
  ): Promise<{
    broker_order_id: string | null;
    filled_price: number | null;
    status: "filled" | "pending";
  }> {
    return request(`/api/accounts/${accountId}/positions/close`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  // ── U4: close position by ID ──
  closePositionById(
    accountId: string,
    positionId: string,
    body: {
      order_type?: "market" | "limit" | "stop";
      limit_price?: number;
      stop_price?: number;
      quantity?: number;
    } = {}
  ): Promise<{
    position_id: string;
    broker_order_id: string | null;
    legs: Array<{ index: number; status: string; filled_price: number | null; fees: number | null }>;
    atomic: boolean;
  }> {
    return request(`/api/accounts/${accountId}/positions/${positionId}/close`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  // ── U4: reconcile positions ──
  reconcilePositions(accountId: string): Promise<{
    matched: Array<{ db_id: string; broker_symbol: string }>;
    untracked: Array<{ symbol: string }>;
    stale: Array<{ id: string }>;
    mismatched: Array<{ db_id: string; broker_qty: number; db_qty: number }>;
  }> {
    return request(`/api/accounts/${accountId}/positions/reconcile`);
  },

  // ── U1: broker asset-type catalog ──
  getBrokerAssetTypes(brokerType: string): Promise<{ asset_types: string[] }> {
    return request<{ asset_types: string[] }>(`/api/brokers/${brokerType}/asset-types`);
  },

  // ── U5: live subscriptions + compare ──
  listLiveSubscriptions(): Promise<LiveSubscription[]> {
    return request<LiveSubscription[]>("/api/live-subscriptions");
  },
  createLiveSubscription(body: {
    account_id?: string;
    provider_type?: string;
    symbol: string;
    asset_class: string;
    tick_retention_hours?: number;
  }): Promise<LiveSubscription> {
    return request<LiveSubscription>("/api/live-subscriptions", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
  estimateLiveSubStorage(
    broker: string,
    symbol: string,
    retention_hours: number
  ): Promise<LiveSubStorageEstimate> {
    const qs = new URLSearchParams({
      broker,
      symbol,
      retention_hours: String(retention_hours),
    });
    return request<LiveSubStorageEstimate>(
      `/api/live-subscriptions/estimate?${qs.toString()}`
    );
  },
  deleteLiveSubscription(id: string): Promise<void> {
    return request<void>(`/api/live-subscriptions/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
  },
  unsubscribeLiveSubscription(id: string): Promise<LiveSubscription | { deleted: true; id: string }> {
    return request<LiveSubscription | { deleted: true; id: string }>(
      `/api/live-subscriptions/${encodeURIComponent(id)}/unsubscribe`,
      { method: "POST" }
    );
  },
  getMarketDataWithSource(
    symbol: string,
    opts: { source?: string; provider?: string; timeframe?: string; bars?: number; limit?: number }
  ): Promise<MarketDataResponse> {
    const qs = new URLSearchParams();
    if (opts.source) qs.set("source", opts.source);
    if (opts.provider) qs.set("provider", opts.provider);
    if (opts.timeframe) qs.set("timeframe", opts.timeframe);
    if (opts.bars !== undefined) qs.set("bars", String(opts.bars));
    if (opts.limit !== undefined) qs.set("limit", String(opts.limit));
    const query = qs.toString();
    return request<MarketDataResponse>(
      `/api/data/market/${encodeURIComponent(symbol)}${query ? `?${query}` : ""}`
    );
  },

  /** Fetch dataset metadata: total bar count and earliest/latest timestamps. */
  getMarketDataMeta(
    symbol: string,
    opts: { source?: string; provider?: string; timeframe?: string }
  ): Promise<{ total_bars: number; first_timestamp: string | null; last_timestamp: string | null }> {
    const qs = new URLSearchParams();
    if (opts.source) qs.set("source", opts.source);
    if (opts.provider) qs.set("provider", opts.provider);
    if (opts.timeframe) qs.set("timeframe", opts.timeframe);
    return request(`/api/data/market/${encodeURIComponent(symbol)}/meta?${qs}`);
  },

  /** Windowed market data fetch — supports start/end filters and a row limit. */
  getMarketDataPaged(
    symbol: string,
    opts: {
      source?: string;
      provider?: string;
      timeframe?: string;
      start?: string;
      end?: string;
      limit?: number;
    }
  ): Promise<MarketDataResponse> {
    const qs = new URLSearchParams();
    if (opts.source) qs.set("source", opts.source);
    if (opts.provider) qs.set("provider", opts.provider);
    if (opts.timeframe) qs.set("timeframe", opts.timeframe);
    if (opts.start) qs.set("start", opts.start);
    if (opts.end) qs.set("end", opts.end);
    if (opts.limit !== undefined) qs.set("limit", String(opts.limit));
    return request<MarketDataResponse>(
      `/api/data/market/${encodeURIComponent(symbol)}?${qs}`
    );
  },

  // ── U6: options chain + submit ──
  getOptionExpiries(accountId: string, underlying: string): Promise<{ expiries: string[] }> {
    return request<{ expiries: string[] }>(
      `/api/accounts/${accountId}/options-chain/expiries?underlying=${encodeURIComponent(underlying)}`
    );
  },
  getOptionChain(accountId: string, underlying: string, expiry: string): Promise<OptionChainResponse> {
    return request<OptionChainResponse>(
      `/api/accounts/${accountId}/options-chain/${encodeURIComponent(expiry)}?underlying=${encodeURIComponent(underlying)}`
    );
  },

  // ── Spec D U1: run backtest modal ──
  createBacktestRun(body: BacktestRunCreate): Promise<BacktestRunRecord> {
    return request<BacktestRunRecord>("/api/backtest-runs", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
  listBacktestRuns(params?: { algorithm_id?: string; limit?: number; offset?: number }): Promise<BacktestRunRecord[]> {
    const qs = new URLSearchParams();
    if (params?.algorithm_id) qs.set("algorithm_id", params.algorithm_id);
    if (params?.limit !== undefined) qs.set("limit", String(params.limit));
    if (params?.offset !== undefined) qs.set("offset", String(params.offset));
    const query = qs.toString();
    // Server returns a bare array (matches /api/algorithms, /api/data/sources convention).
    return request<BacktestRunRecord[]>(`/api/backtest-runs${query ? `?${query}` : ""}`);
  },
  getBacktestRun(id: string): Promise<BacktestRunRecord> {
    return request<BacktestRunRecord>(`/api/backtest-runs/${id}`);
  },
  getBacktestReport(id: string): Promise<BacktestReport> {
    return request<BacktestReport>(`/api/backtest-runs/${id}/report`);
  },
  getBacktestEquityWindow(
    id: string, params: { from: string; to: string; resolution?: "1min" | "1hour" | "1day" | "auto" },
  ): Promise<BacktestEquityWindow> {
    const qs = new URLSearchParams();
    qs.set("from", params.from);
    qs.set("to", params.to);
    qs.set("resolution", params.resolution ?? "auto");
    return request<BacktestEquityWindow>(`/api/backtest-runs/${id}/equity?${qs.toString()}`);
  },
  getBacktestTrades(id: string, params?: { limit?: number; offset?: number }): Promise<{ items: unknown[] }> {
    const qs = new URLSearchParams();
    if (params?.limit !== undefined) qs.set("limit", String(params.limit));
    if (params?.offset !== undefined) qs.set("offset", String(params.offset));
    const query = qs.toString();
    return request(`/api/backtest-runs/${id}/trades${query ? `?${query}` : ""}`);
  },
  deleteBacktestRun(id: string): Promise<void> {
    return request<void>(`/api/backtest-runs/${id}`, { method: "DELETE" });
  },

  // ── M4.5: Activity feeds ──
  listWorkerActivity(
    workerId: string,
    params?: { limit?: number; before?: string; severity?: string; event_types?: string; kind?: string },
  ): Promise<{ items: ActivityRow[] }> {
    const qs = new URLSearchParams();
    if (params?.limit !== undefined) qs.set("limit", String(params.limit));
    if (params?.before) qs.set("before", params.before);
    if (params?.severity) qs.set("severity", params.severity);
    if (params?.event_types) qs.set("event_types", params.event_types);
    if (params?.kind && params.kind !== "all") qs.set("kind", params.kind);
    const query = qs.toString();
    return request<{ items: ActivityRow[] }>(
      `/api/workers/${workerId}/activity${query ? `?${query}` : ""}`,
    );
  },

  listDeploymentActivity(
    deploymentId: string,
    params?: { limit?: number; before?: string; severity?: string; event_types?: string; kind?: string },
  ): Promise<{ items: ActivityRow[] }> {
    const qs = new URLSearchParams();
    if (params?.limit !== undefined) qs.set("limit", String(params.limit));
    if (params?.before) qs.set("before", params.before);
    if (params?.severity) qs.set("severity", params.severity);
    if (params?.event_types) qs.set("event_types", params.event_types);
    if (params?.kind && params.kind !== "all") qs.set("kind", params.kind);
    const query = qs.toString();
    return request<{ items: ActivityRow[] }>(
      `/api/deployments/${deploymentId}/activity${query ? `?${query}` : ""}`,
    );
  },

  // ── M6.4: Deployment trades ──
  listDeploymentTrades(
    id: string,
    params?: { limit?: number; offset?: number; run_id?: string },
  ): Promise<{ items: DeploymentTrade[] }> {
    const qs = new URLSearchParams();
    if (params?.limit !== undefined) qs.set("limit", String(params.limit));
    if (params?.offset !== undefined) qs.set("offset", String(params.offset));
    if (params?.run_id) qs.set("run_id", params.run_id);
    const suffix = qs.toString() ? `?${qs}` : "";
    return request<{ items: DeploymentTrade[] }>(`/api/deployments/${id}/trades${suffix}`);
  },
};

// ── U5: live subscriptions + compare ──
export interface SubscriptionConsumer {
  id: string;
  consumer_type: "manual" | "algo";
  consumer_id: string | null;
  created_at: string | null;
  algorithm_id: string | null;
  algorithm_name: string | null;
}

export interface LiveSubscription {
  id: string;
  account_id: string | null;
  account_name: string | null;
  provider_type: string | null;
  broker: string;
  symbol: string;
  asset_class: string;
  tick_retention_hours: number;
  tick_rate_per_min: number | null;
  status: string;
  created_at: string | null;
  last_tick_at: string | null;
  error_message: string | null;
  consumers: SubscriptionConsumer[];
}

export interface LiveSubStorageEstimate {
  broker: string;
  symbol: string;
  retention_hours: number;
  projected_bytes: number;
  projected_human: string;
  tick_rate_per_min: number | null;
  source: "estimated" | "observed";
}

// ── Spec D U1: run backtest modal ──
export interface BacktestRunCreate {
  algorithm_id: string;
  date_range_start: string;
  date_range_end: string;
  initial_cash: number;
  config_overrides?: Record<string, unknown>;
  buy_trading_fees?: Array<{ flat_fee: number; percent_fee: number; maker: boolean; taker: boolean }>;
  sell_trading_fees?: Array<{ flat_fee: number; percent_fee: number; maker: boolean; taker: boolean }>;
  slippage_model?: {
    market_bps: number;
    limit_bps: number;
    use_bar_range: boolean;
    volume_impact_bps_per_pct: number;
  };
  benchmark_symbol?: string;
  benchmark_source?: string;
  parameter_set_id?: string;
}

export interface BacktestRunRecord {
  id: string;
  algorithm_id: string;
  status: string;
  date_range_start: string;
  date_range_end: string;
  initial_cash: number;
  config_overrides: Record<string, unknown> | null;
  buy_trading_fees: unknown[] | null;
  sell_trading_fees: unknown[] | null;
  slippage_model: Record<string, unknown> | null;
  benchmark_symbol: string | null;
  benchmark_source: string | null;
  progress_message: string | null;
  progress_pct: number | null;
  error_message: string | null;
  total_return: number | null;
  cagr: number | null;
  volatility: number | null;
  sharpe_ratio: number | null;
  sortino_ratio: number | null;
  calmar_ratio: number | null;
  max_drawdown: number | null;
  max_drawdown_date: string | null;
  romad: number | null;
  total_fees_paid: number | null;
  total_slippage_dollars: number | null;
  trade_count: number | null;
  win_rate: number | null;
  profit_factor: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  expectancy: number | null;
  longest_drawdown_days: number | null;
  longest_winning_streak: number | null;
  longest_losing_streak: number | null;
  download_ids: string[] | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

// ── U6: options chain + submit ──
export interface OptionChainContract {
  strike: number;
  right: "call" | "put";
  occ_symbol: string;
  bid: number | null;
  ask: number | null;
  last: number | null;
  iv: number | null;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  open_interest: number | null;
  volume: number | null;
}

export interface OptionChainResponse {
  underlying: string;
  spot: number;
  expiry: string;
  as_of: string | null;
  contracts: OptionChainContract[];
}
