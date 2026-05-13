// ─── Core Domain Types ────────────────────────────────────────────────────────

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

export interface Worker {
  id: string;
  name: string;
  tailscale_ip: string;
  status: string;
  last_heartbeat: string | null;
  max_algorithms: number;
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
}

export interface EquityPoint {
  date: string;
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

export interface SettingsStatus {
  github_pat_set: boolean;
  discord_bot_token_set: boolean;
  polygon_api_key_set: boolean;
  theta_data_set: boolean;
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
