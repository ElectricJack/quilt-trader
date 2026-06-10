// ─── Core Domain Types ────────────────────────────────────────────────────────

export interface Account {
  id: string;
  name: string;
  broker_type: string;
  environment: "paper" | "live";
  supported_asset_types: string[];
  options_level: number | null;
  account_features: string[] | null;
  pdt_mode: string;
  show_in_overview: boolean;
  locked_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface Worker {
  id: string;
  name: string;
  tailscale_ip: string;
  status: string;
  last_heartbeat: string | null;
  max_algorithms: number;
  install_status: "pending" | "claimed";
  install_token: string | null;
  created_at: string;
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
  data_dependencies: Record<string, unknown>[] | null;
  config_schema: Record<string, unknown> | null;
  custom_events: Record<string, unknown>[] | null;
  install_status: string;
  install_error: string | null;
  installed_at: string | null;
  updated_at: string | null;
}

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
  lifetime_metrics: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
  // Enriched fields from /api/instances response:
  algorithm_name?: string | null;
  account_name?: string | null;
  today_pnl?: number | null;
  pnl_sparkline?: number[] | null;
}

// Deployment is the public-facing alias for AlgorithmInstance (used by /api/deployments/*).
// AlgorithmInstance is kept for backward-compat with existing code.
export interface Deployment {
  id: string;
  algorithm_id: string;
  account_id: string;
  worker_id: string;
  algorithm_name: string;
  account_name: string;
  worker_name: string;
  status: string;
  active_run_id: string | null;
  config_values: Record<string, unknown> | null;
  lifetime_metrics: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface EquityPoint {
  timestamp: string;
  equity: number;
}

export interface AlgorithmRun {
  id: string;
  instance_id: string;
  run_number: number;
  status: string;
  started_at: string | null;
  stopped_at: string | null;
  starting_equity: number | null;
  ending_equity: number | null;
  net_pnl: number | null;
  unrealized_pnl: number | null;
  total_fees: number;
  total_slippage: number;
  trade_count: number;
  metrics: Record<string, unknown> | null;
  equity_curve: EquityPoint[] | null;
}

export interface PerformanceMetrics {
  total_pnl: number;
  win_rate: number;
  sharpe_ratio: number | null;
  max_drawdown: number | null;
  trade_count: number;
}

export interface PositionLeg {
  symbol: string;
  quantity: number;
  side: string;
  avg_price: number;
  current_price: number | null;
  unrealized_pnl: number | null;
}

export interface Position {
  id: string;
  instance_id: string;
  symbol: string;
  legs: PositionLeg[];
  opened_at: string;
  closed_at: string | null;
  realized_pnl: number | null;
  status: string;
}

export interface TradeLogEntry {
  id: string;
  instance_id: string;
  timestamp: string;
  action: string;
  symbol: string;
  quantity: number;
  price: number;
  notes: string | null;
}

export interface SystemEvent {
  id: string;
  source_type: string;
  source_id: string | null;
  event_type: string;
  severity: string;
  payload: Record<string, unknown> | null;
  timestamp: string | null;
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
  description: string | null;
  clone_url: string;
  html_url: string;
}

export interface AlgorithmGitStatus {
  default_branch: string;
  head_sha: string;
  commits_behind: number;
  current_sha: string | null;
}

export interface SettingsStatus {
  github_pat_set: boolean;
  discord_bot_token_set: boolean;
  polygon_api_key_set: boolean;
  theta_data_set: boolean;
  tailscale_authkey_set: boolean;
  coordinator_ip: string | null;
  // Polygon tier overrides (plaintext, nullable when defaulted)
  polygon_min_request_interval_s: string | null;
  polygon_concurrency: string | null;
  // FMP (Financial Modeling Prep) — datasets framework
  fmp_api_key_set: boolean;
  fmp_daily_quota_limit: string | null;
  fmp_min_request_interval_s: string | null;
  dataset_quota_reset_tz: string | null;
}

export interface FmpTierBody {
  daily_quota_limit?: number | null;
  min_request_interval_s?: number | null;
  quota_reset_tz?: string | null;
}

export interface HealthResponse {
  status: string;
  version: string;
}

export interface InstalledAlgorithmResponse {
  id: string;
  name: string;
  description: string | null;
  version: string | null;
  install_status: string;
  repo_url: string;
}

export interface CashFlow {
  id: string;
  account_id: string;
  type: string;
  amount: number;
  timestamp: string | null;
  notes: string | null;
}

export interface BacktestComparison {
  id: string;
  instance_id: string;
  algorithm_id: string;
  time_range_start: string | null;
  time_range_end: string | null;
  total_ticks: number;
  matching_ticks: number;
  match_percentage: number;
  divergences: Record<string, unknown>[] | null;
  summary: string | null;
  created_at: string | null;
}

export interface DataAvailability {
  provider: string;
  symbols: string[];
  timeframes: string[];
}

export interface MarketDataDownload {
  id: string;
  symbols: string[];
  date_range_start: string;
  date_range_end: string;
  provider: string;
  data_type: string;
  timeframe: string;
  status: string;
  progress_current: number;
  progress_total: number;
  error_message: string | null;
  progress_message: string | null;
  current_symbol_pct: number | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface AccountSnapshot {
  id: string;
  account_id: string;
  timestamp: string;
  total_value: number;
  cash: number;
  positions_value: number;
  net_deposits_cumulative: number;
  source: string;
}

export interface PortfolioEquityPoint {
  timestamp: string;
  value: number;
}

export interface PortfolioEquityAccount {
  account_id: string;
  account_name: string;
  points: PortfolioEquityPoint[];
}

export interface PortfolioEquityResponse {
  accounts: PortfolioEquityAccount[];
}

export interface PortfolioKpis {
  total_equity: number;
  today_pnl: number;
  today_pnl_pct: number;
  trades_today: number;
  trades_today_wins: number;
  trades_today_losses: number;
  win_rate: number;
  win_rate_7d_avg: number;
  open_positions: number;
  open_positions_long: number;
  open_positions_short: number;
  open_risk: number;
  open_risk_pct_equity: number;
  deployed_pct: number;
  deployed_usd: number;
  buying_power: number;
  buying_power_pct: number;
}

export interface AllocSegment {
  key: string;
  label: string;
  value_usd: number;
  percent: number;
  color: string;
}

export interface AllocationResponse {
  by_class: AllocSegment[];
  by_symbol: AllocSegment[];
}

export interface OpenPositionRow {
  id: string;
  instance_id: string | null;
  account_id: string;
  algorithm_name: string | null;
  status: string;
  symbol: string | null;
  side: string | null;
  quantity: number | null;
  avg_price: number | null;
  current_price: number | null;
  asset_type: string | null;
  unrealized_pnl: number | null;
  net_pnl: number | null;
  net_cost: number;
  extra_legs: number;
  opened_at: string | null;
}

export interface TradeRow {
  id: string;
  instance_id: string | null;
  account_id: string;
  algorithm_name: string | null;
  timestamp: string | null;
  symbol: string;
  asset_type: string;
  side: string;
  quantity: number;
  filled_price: number;
  notional: number;
  fees: number;
}

export interface AlertItem {
  kind: "event" | "backtest";
  id: string;
  severity: string;
  label: string;
  source_name: string;
  timestamp: string | null;
  link_path: string | null;
  pill: string;
  pill_color: "warn" | "err" | "backtest";
}

export interface AccountSnapshotLatestItem {
  account_id: string;
  account_name: string;
  broker_type: string;
  latest: {
    timestamp: string;
    total_value: number;
    cash: number;
    positions_value: number;
  };
  prior: {
    timestamp: string;
    total_value: number;
    cash: number;
    positions_value: number;
  } | null;
  day_pct: number | null;
}

export interface MarketDataBar {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface MarketDataResponse {
  data: MarketDataBar[];
  /** Total bars available in the dataset (before limit/windowing). */
  total?: number;
  /** True when the response was truncated to the requested limit. */
  truncated?: boolean;
}

// ── Backtest Report ──

export interface BacktestKeyMetrics {
  total_return: number;
  cagr: number;
  volatility: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  max_drawdown: number;
  romad: number;
  longest_drawdown_days: number;
  // Tail risk
  daily_var?: number;
  daily_cvar?: number;
  skew?: number;
  kurtosis?: number;
  // Period returns
  ytd?: number; "1y"?: number; "3y"?: number;
  // Distribution
  best_day?: number; worst_day?: number;
  best_month?: number; worst_month?: number;
  // Win rates
  time_in_market?: number; win_days?: number; win_month?: number;
  // vs benchmark (strategy only)
  beta?: number; alpha?: number; correlation?: number;
  [key: string]: number | undefined;
}

export interface BacktestRollingPoint {
  timestamp: string;
  sharpe: number | null;
  sortino: number | null;
  vol: number | null;
  beta: number | null;
}

export interface BacktestReport {
  id: string;
  algorithm_id: string;
  status: string;
  date_range_start: string | null;
  date_range_end: string | null;
  initial_cash: number;
  config_overrides: Record<string, unknown> | null;
  benchmark_symbol: string | null;
  benchmark_source: string | null;
  progress_message: string | null;
  progress_pct: number | null;
  key_metrics: { strategy: BacktestKeyMetrics; benchmark: BacktestKeyMetrics } | null;
  equity_curve: { timestamp: string; portfolio_value: number; cash?: number }[] | null;
  benchmark_equity_curve: { timestamp: string; value: number }[] | null;
  drawdown_curve: { timestamp: string; drawdown_pct: number }[] | null;
  rolling_metrics: { window_days: number; points: BacktestRollingPoint[] } | null;
  monthly_returns_matrix: { years: number[]; cells: [number, number, number][] } | null;
  eoy_returns: {
    year: number; strategy_pct: number; benchmark_pct: number | null;
    multiplier: number | null; won: boolean;
  }[] | null;
  drawdown_periods: {
    start: string; trough: string; recovered: string | null;
    depth: number; days: number;
  }[] | null;
}

export interface BacktestEquityWindow {
  resolution: "1min" | "1hour" | "1day";
  items: { ts: string; portfolio_value: number; cash: number }[];
}

// ── Deployment Report (M6.2) ──

export interface DeploymentReport {
  deployment_id: string;
  generated_at: string;
  total_return: number | null;
  cagr: number | null;
  volatility: number | null;
  sharpe_ratio: number | null;
  sortino_ratio: number | null;
  calmar_ratio: number | null;
  max_drawdown: number | null;
  romad: number | null;
  trade_count: number | null;
  win_rate: number | null;
  profit_factor: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  expectancy: number | null;
  longest_drawdown_days: number | null;
  equity_curve: { timestamp: string | null; portfolio_value: number | null }[] | null;
  drawdown_curve: unknown[] | null;
  drawdown_periods: unknown[] | null;
  key_metrics: {
    strategy?: Record<string, number | null | undefined>;
    benchmark?: Record<string, number | null | undefined>;
  } | null;
  rolling_metrics: Record<string, unknown> | null;
  monthly_returns_matrix: Record<string, unknown> | null;
  eoy_returns: unknown[] | null;
  runs_index: {
    run_id: string;
    run_number: number;
    started_at: string | null;
    stopped_at: string | null;
    status: string;
  }[] | null;
}

// ── Deployment Trade (M6.4) ──

export interface DeploymentTrade {
  id: string;
  timestamp: string;
  symbol: string;
  asset_type: string;
  side: string;
  quantity: number;
  requested_price: number | null;
  fill_price: number | null;
  fees: number | null;
  slippage_dollars: number | null;
  realized_pnl: number | null;
}

export type ActivityRow = {
  id: string;
  worker_id: string;
  instance_id: string | null;
  timestamp: string; // ISO UTC
  kind: "event" | "log";
  event_type: string | null;
  severity: "debug" | "info" | "warn" | "error";
  logger_name: string | null;
  message: string | null;
  payload: Record<string, unknown> | null;
};
