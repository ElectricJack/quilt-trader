import {
  useQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { api } from "./client";
import type {
  AccountCreate,
  AccountUpdate,
  WorkerCreate,
  WorkerUpdate,
  InstanceCreate,
  InstanceUpdate,
  CashFlowCreate,
  DownloadCreate,
  EventParams,
} from "./client";

// ─── Query Keys ────────────────────────────────────────────────────────────────

export const keys = {
  accounts: () => ["accounts"] as const,
  account: (id: string) => ["accounts", id] as const,
  workers: () => ["workers"] as const,
  worker: (id: string) => ["workers", id] as const,
  algorithms: () => ["algorithms"] as const,
  algorithm: (id: string) => ["algorithms", id] as const,
  instances: (algoId: string) => ["algorithms", algoId, "instances"] as const,
  instance: (id: string) => ["instances", id] as const,
  allInstances: () => ["instances"] as const,
  runs: (instanceId: string) => ["instances", instanceId, "runs"] as const,
  run: (id: string) => ["runs", id] as const,
  cashFlows: (accountId: string) => ["accounts", accountId, "cash-flows"] as const,
  backtests: () => ["backtests"] as const,
  backtest: (id: string) => ["backtests", id] as const,
  availableData: () => ["data", "available"] as const,
  downloads: () => ["data", "downloads"] as const,
  download: (id: string) => ["data", "downloads", id] as const,
  events: (params: EventParams) => ["events", params] as const,
  settings: () => ["settings"] as const,
  repos: () => ["repos"] as const,
};

// ─── Accounts ─────────────────────────────────────────────────────────────────

export function useAccounts() {
  return useQuery({ queryKey: keys.accounts(), queryFn: api.listAccounts });
}

export function useAccount(id: string) {
  return useQuery({
    queryKey: keys.account(id),
    queryFn: () => api.getAccount(id),
    enabled: !!id,
  });
}

export function useCreateAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AccountCreate) => api.createAccount(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.accounts() });
    },
  });
}

export function useUpdateAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: AccountUpdate }) =>
      api.updateAccount(id, body),
    onSuccess: (_data, { id }) => {
      void qc.invalidateQueries({ queryKey: keys.accounts() });
      void qc.invalidateQueries({ queryKey: keys.account(id) });
    },
  });
}

export function useDeleteAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteAccount(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.accounts() });
    },
  });
}

export function useBrokerInfo(id: string) {
  return useQuery({
    queryKey: ["accounts", id, "broker-info"] as const,
    queryFn: () => api.getBrokerInfo(id),
    enabled: !!id,
    staleTime: 30_000,
    retry: false, // Surface broker errors immediately rather than hammering
  });
}

export function useSyncAccount(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (since?: string) => api.syncAccount(id, since),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["accounts", id, "broker-info"] });
      void qc.invalidateQueries({ queryKey: keys.cashFlows(id) });
      void qc.invalidateQueries({ queryKey: ["accounts", id, "trades"] });
      void qc.invalidateQueries({ queryKey: ["accounts", id, "equity-curve"] });
      void qc.invalidateQueries({ queryKey: ["accounts", "snapshots", "latest"] });
    },
  });
}

export function useAccountTrades(id: string, limit = 100) {
  return useQuery({
    queryKey: ["accounts", id, "trades", limit] as const,
    queryFn: () => api.listAccountTrades(id, limit),
    enabled: !!id,
    staleTime: 30_000,
  });
}

export function useAccountEquityCurve(id: string, since?: string) {
  return useQuery({
    queryKey: ["accounts", id, "equity-curve", since ?? "default"] as const,
    queryFn: () => api.getEquityCurve(id, since),
    enabled: !!id,
    staleTime: 60_000,
  });
}

// ─── Workers ──────────────────────────────────────────────────────────────────

export function useWorkers() {
  return useQuery({ queryKey: keys.workers(), queryFn: api.listWorkers });
}

export function useWorker(id: string) {
  return useQuery({
    queryKey: keys.worker(id),
    queryFn: () => api.getWorker(id),
    enabled: !!id,
  });
}

export function useCreateWorker() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: WorkerCreate) => api.createWorker(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.workers() });
    },
  });
}

export function useUpdateWorker() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: WorkerUpdate }) =>
      api.updateWorker(id, body),
    onSuccess: (_data, { id }) => {
      void qc.invalidateQueries({ queryKey: keys.workers() });
      void qc.invalidateQueries({ queryKey: keys.worker(id) });
    },
  });
}

export function useDeleteWorker() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteWorker(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.workers() });
    },
  });
}

export function useWorkerInstallCommand(id: string, enabled = true) {
  return useQuery({
    queryKey: ["workers", id, "install-command"] as const,
    queryFn: () => api.getWorkerInstallCommand(id),
    enabled: !!id && enabled,
    retry: false,
    staleTime: Infinity,
  });
}

export function useRegenerateWorkerInstallToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.regenerateWorkerInstallToken(id),
    onSuccess: (_data, id) => {
      void qc.invalidateQueries({ queryKey: keys.workers() });
      void qc.invalidateQueries({ queryKey: keys.worker(id) });
      void qc.invalidateQueries({ queryKey: ["workers", id, "install-command"] });
    },
  });
}

// ─── Algorithms ───────────────────────────────────────────────────────────────

export function useAlgorithms() {
  return useQuery({
    queryKey: keys.algorithms(),
    queryFn: api.listAlgorithms,
  });
}

export function useAlgorithm(id: string) {
  return useQuery({
    queryKey: keys.algorithm(id),
    queryFn: () => api.getAlgorithm(id),
    enabled: !!id,
  });
}

export function useDeleteAlgorithm() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteAlgorithm(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.algorithms() });
    },
  });
}

export function useAlgorithmGitStatus(id: string) {
  return useQuery({
    queryKey: ["algorithms", id, "git-status"] as const,
    queryFn: () => api.getAlgorithmGitStatus(id),
    enabled: !!id,
    staleTime: 60_000,
    retry: 1, // GitHub API hiccups; don't hammer
  });
}

export function useUpdateAlgorithm() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.updateAlgorithm(id),
    onSuccess: (_data, id) => {
      void qc.invalidateQueries({ queryKey: keys.algorithms() });
      void qc.invalidateQueries({ queryKey: ["algorithms", id, "git-status"] });
    },
  });
}

// ─── Instances ────────────────────────────────────────────────────────────────

export function useInstances(algorithmId: string) {
  return useQuery({
    queryKey: keys.instances(algorithmId),
    queryFn: () => api.listInstances(algorithmId),
    enabled: !!algorithmId,
  });
}

export function useInstance(instanceId: string) {
  return useQuery({
    queryKey: keys.instance(instanceId),
    queryFn: () => api.getInstance(instanceId),
    enabled: !!instanceId,
  });
}

export function useCreateInstance(algorithmId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: InstanceCreate) =>
      api.createInstance(algorithmId, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.instances(algorithmId) });
    },
  });
}

export function useUpdateInstance() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: InstanceUpdate }) =>
      api.updateInstance(id, body),
    onSuccess: (_data, { id }) => {
      void qc.invalidateQueries({ queryKey: keys.instance(id) });
      void qc.invalidateQueries({ queryKey: keys.allInstances() });
    },
  });
}

export function useDeleteInstance() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteInstance(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.allInstances() });
    },
  });
}

// ─── Install Algorithm ────────────────────────────────────────────────────────

export function useInstallAlgorithm() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (fullName: string) => api.installAlgorithm(fullName),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.algorithms() });
    },
  });
}

// ─── Events ───────────────────────────────────────────────────────────────────

export function useEvents(params: EventParams = {}) {
  return useQuery({
    queryKey: keys.events(params),
    queryFn: () => api.listEvents(params),
  });
}

// ─── Settings ─────────────────────────────────────────────────────────────────

export function useSettings() {
  return useQuery({ queryKey: keys.settings(), queryFn: api.getSettings });
}

// ─── GitHub Repos ─────────────────────────────────────────────────────────────

export function useGithubRepos(enabled = false) {
  return useQuery({
    queryKey: keys.repos(),
    queryFn: api.listRepos,
    enabled,
  });
}

// ─── Runs ─────────────────────────────────────────────────────────────────────

export function useRuns(instanceId: string) {
  return useQuery({
    queryKey: keys.runs(instanceId),
    queryFn: () => api.listRuns(instanceId),
    enabled: !!instanceId,
  });
}

export function useRun(id: string) {
  return useQuery({
    queryKey: keys.run(id),
    queryFn: () => api.getRun(id),
    enabled: !!id,
  });
}

// ─── Cash Flows ───────────────────────────────────────────────────────────────

export function useCashFlows(accountId: string) {
  return useQuery({
    queryKey: keys.cashFlows(accountId),
    queryFn: () => api.listCashFlows(accountId),
    enabled: !!accountId,
  });
}

export function useCreateCashFlow(accountId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CashFlowCreate) => api.createCashFlow(accountId, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.cashFlows(accountId) });
    },
  });
}

// ─── Downloads ───────────────────────────────────────────────────────────────

export function useDownloads() {
  return useQuery({
    queryKey: keys.downloads(),
    queryFn: api.listDownloads,
  });
}

export function useCreateDownload() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: DownloadCreate) => api.createDownload(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.downloads() });
    },
  });
}

export function useCancelDownload() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.cancelDownload(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.downloads() });
    },
  });
}

export function useDeleteDownload() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteDownload(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.downloads() });
    },
  });
}

export function useClearDownloads() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (status?: string) => api.clearDownloads(status),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.downloads() });
    },
  });
}

// ─── Backtests ────────────────────────────────────────────────────────────────

export function useBacktests() {
  return useQuery({
    queryKey: keys.backtests(),
    queryFn: api.listBacktests,
  });
}

export function useBacktest(id: string) {
  return useQuery({
    queryKey: keys.backtest(id),
    queryFn: () => api.getBacktest(id),
    enabled: !!id,
  });
}

// ─── All Instances ────────────────────────────────────────────────────────────

export function useAllInstances() {
  return useQuery({
    queryKey: keys.allInstances(),
    queryFn: api.listAllInstances,
  });
}

// ─── Available Data ───────────────────────────────────────────────────────────

export function useAvailableData() {
  return useQuery({
    queryKey: keys.availableData(),
    queryFn: api.listAvailableData,
  });
}

// ─── Scrapers ─────────────────────────────────────────────────────────────────

export function useScrapers() {
  return useQuery({
    queryKey: ["scrapers"] as const,
    queryFn: api.listScrapers,
    staleTime: 30_000,
  });
}

export function useInstallScraper() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { repo_url: string; name?: string }) => api.installScraper(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["scrapers"] });
      void qc.invalidateQueries({ queryKey: ["data-sources"] });
    },
  });
}

export function useDeleteScraper() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => api.deleteScraper(name),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["scrapers"] });
      void qc.invalidateQueries({ queryKey: ["data-sources"] });
    },
  });
}

export function useRunScraper() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => api.runScraper(name),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["scrapers"] });
      void qc.invalidateQueries({ queryKey: ["data-sources"] });
    },
  });
}

export function useDataSources(type?: string) {
  return useQuery({
    queryKey: ["data-sources", type ?? "all"] as const,
    queryFn: () => api.listDataSources(type),
    staleTime: 30_000,
  });
}

export function useCustomData(name: string | null) {
  return useQuery({
    queryKey: ["custom-data", name] as const,
    queryFn: () => api.getCustomData(name!),
    enabled: !!name,
    staleTime: 30_000,
  });
}

export function useMarketData(provider: string | null, symbol: string | null, timeframe: string | null) {
  return useQuery({
    queryKey: ["market-data", provider, symbol, timeframe] as const,
    queryFn: () => api.getMarketData(provider!, symbol!, timeframe!),
    enabled: !!provider && !!symbol && !!timeframe,
    staleTime: 60_000,
  });
}

// ─── Settings Mutations ──────────────────────────────────────────────────────

export function useSetGithubPat() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (value: string) => api.setGithubPat(value),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.settings() });
    },
  });
}

export function useSetDiscordToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (value: string) => api.setDiscordToken(value),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.settings() });
    },
  });
}

export function useSetPolygonKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (value: string) => api.setPolygonKey(value),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.settings() });
    },
  });
}

export function useSetThetaData() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ username, password }: { username: string; password: string }) =>
      api.setThetaData(username, password),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.settings() });
    },
  });
}

export function useDeleteGithubPat() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.deleteGithubPat(),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.settings() });
    },
  });
}

export function useDeleteDiscordToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.deleteDiscordToken(),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.settings() });
    },
  });
}

export function useDeletePolygonKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.deletePolygonKey(),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.settings() });
    },
  });
}

export function useDeleteThetaData() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.deleteThetaData(),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.settings() });
    },
  });
}

// ─── Portfolio ────────────────────────────────────────────────────────────────

export function usePortfolioEquity(range: "1d" | "1w" | "1m" | "all" = "1m") {
  return useQuery({
    queryKey: ["portfolio", "equity", range] as const,
    queryFn: () => api.portfolioEquity(range),
    staleTime: 30_000,
  });
}

export function usePortfolioKpis() {
  return useQuery({
    queryKey: ["portfolio", "kpis"] as const,
    queryFn: api.portfolioKpis,
    staleTime: 15_000,
    refetchInterval: 30_000,
  });
}

export function usePortfolioAllocation() {
  return useQuery({
    queryKey: ["portfolio", "allocation"] as const,
    queryFn: api.portfolioAllocation,
    staleTime: 60_000,
  });
}

// ─── Positions ────────────────────────────────────────────────────────────────

export function useOpenPositions(limit = 10) {
  return useQuery({
    queryKey: ["positions", "open", limit] as const,
    queryFn: () => api.listOpenPositions(limit),
    staleTime: 15_000,
    refetchInterval: 30_000,
  });
}

// ─── Trades ───────────────────────────────────────────────────────────────────

export function useRecentTrades(limit = 10) {
  return useQuery({
    queryKey: ["trades", "recent", limit] as const,
    queryFn: () => api.listRecentTrades(limit),
    staleTime: 15_000,
    refetchInterval: 30_000,
  });
}

// ─── Alerts ───────────────────────────────────────────────────────────────────

export function useAlerts(limit = 10) {
  return useQuery({
    queryKey: ["alerts", limit] as const,
    queryFn: () => api.listAlerts(limit),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

// ─── Account snapshots ────────────────────────────────────────────────────────

export function useAccountSnapshotsLatest() {
  return useQuery({
    queryKey: ["accounts", "snapshots", "latest"] as const,
    queryFn: api.accountSnapshotsLatest,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

// ── U3: install algorithm from URL ──

export function useInstallAlgorithmFromUrl() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (repo_url: string) => api.installAlgorithmFromUrl(repo_url),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["algorithms"] }),
  });
}

// ── U1: broker asset-type catalog ──
export function useBrokerAssetTypes(brokerType: string | null | undefined) {
  return useQuery({
    queryKey: ["brokerAssetTypes", brokerType],
    queryFn: async () => {
      if (!brokerType) return [];
      const r = await api.getBrokerAssetTypes(brokerType);
      return r.asset_types;
    },
    enabled: !!brokerType,
  });
}

// ── U2: open position ──

export function useOpenPosition(accountId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof api.openPosition>[1]) =>
      api.openPosition(accountId, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["brokerInfo", accountId] });
    },
  });
}

// ── U5: live subscriptions + compare ──

export function useLiveSubscriptions() {
  return useQuery({
    queryKey: ["live-subs"] as const,
    queryFn: api.listLiveSubscriptions,
    staleTime: 15_000,
    refetchInterval: 30_000,
  });
}

export function useCreateLiveSubscription() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof api.createLiveSubscription>[0]) =>
      api.createLiveSubscription(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["live-subs"] });
    },
  });
}

export function useDeleteLiveSubscription() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteLiveSubscription(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["live-subs"] });
    },
  });
}

export function useLiveSubStorageEstimate(
  broker: string | null,
  symbol: string | null,
  retentionHours: number
) {
  return useQuery({
    queryKey: ["live-sub-estimate", broker, symbol, retentionHours] as const,
    queryFn: () => api.estimateLiveSubStorage(broker!, symbol!, retentionHours),
    enabled: !!broker && !!symbol,
    staleTime: 60_000,
  });
}

/** Fetch market data, allowing `source` (provider or live source like `alpaca_live`). */
export function useMarketDataSource(
  source: string | null,
  symbol: string | null,
  timeframe: string | null,
  bars?: number
) {
  return useQuery({
    queryKey: ["market-data-source", source, symbol, timeframe, bars ?? null] as const,
    queryFn: () =>
      api.getMarketDataWithSource(symbol!, {
        source: source!,
        timeframe: timeframe!,
        ...(bars !== undefined ? { bars } : {}),
      }),
    enabled: !!source && !!symbol && !!timeframe,
    staleTime: 30_000,
  });
}

// ── U6: options chain + submit ──

export function useOptionExpiries(accountId: string, underlying: string | null) {
  return useQuery({
    queryKey: ["option-expiries", accountId, underlying] as const,
    queryFn: () => api.getOptionExpiries(accountId, underlying!),
    enabled: !!accountId && !!underlying,
    staleTime: 60_000,
  });
}

export function useOptionChain(
  accountId: string,
  underlying: string | null,
  expiry: string | null
) {
  return useQuery({
    queryKey: ["option-chain", accountId, underlying, expiry] as const,
    queryFn: () => api.getOptionChain(accountId, underlying!, expiry!),
    enabled: !!accountId && !!underlying && !!expiry,
    staleTime: 30_000,
  });
}

// ── Spec D U1: run backtest modal ──

export function useCreateBacktestRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof api.createBacktestRun>[0]) =>
      api.createBacktestRun(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["backtest-runs"] });
    },
  });
}

export function useBacktestRuns(algorithm_id?: string) {
  return useQuery({
    queryKey: ["backtest-runs", algorithm_id ?? null] as const,
    queryFn: () => api.listBacktestRuns(algorithm_id ? { algorithm_id } : undefined),
  });
}

export function useBacktestRun(id: string, opts?: { refetchInterval?: number }) {
  return useQuery({
    queryKey: ["backtest-run", id] as const,
    queryFn: () => api.getBacktestRun(id),
    enabled: !!id,
    refetchInterval: opts?.refetchInterval,
  });
}

export function useBacktestEquityCurve(id: string) {
  return useQuery({
    queryKey: ["backtest-equity", id] as const,
    queryFn: () => api.getBacktestEquityCurve(id),
    enabled: !!id,
  });
}

export function useBacktestTrades(id: string, limit = 500, offset = 0) {
  return useQuery({
    queryKey: ["backtest-trades", id, limit, offset] as const,
    queryFn: () => api.getBacktestTrades(id, { limit, offset }),
    enabled: !!id,
  });
}

export function useDeleteBacktestRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteBacktestRun(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["backtest-runs"] });
    },
  });
}
