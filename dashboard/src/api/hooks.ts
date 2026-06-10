import { useEffect, useState } from "react";
import {
  useQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { api } from "./client";
import type { Deployment } from "../types";
import { wsManager } from "./websocket";
import type {
  AccountCreate,
  AccountUpdate,
  WorkerCreate,
  WorkerUpdate,
  InstanceCreate,
  CashFlowCreate,
  DownloadCreate,
  EventParams,
  FillGapsRequest,
  GoalCreate,
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
  cashFlows: (accountId: string) => ["accounts", accountId, "cash-flows"] as const,
  backtests: () => ["backtests"] as const,
  backtest: (id: string) => ["backtests", id] as const,
  downloads: () => ["data", "downloads"] as const,
  download: (id: string) => ["data", "downloads", id] as const,
  events: (params: EventParams) => ["events", params] as const,
  settings: () => ["settings"] as const,
  repos: () => ["repos"] as const,
  deployments: (params?: { algorithm_id?: string; worker_id?: string; account_id?: string }) => ["deployments", params] as const,
  deployment: (id: string) => ["deployments", id] as const,
  deploymentRuns: (id: string) => ["deployments", id, "runs"] as const,
  researchSessions: () => ["research", "sessions"] as const,
  researchSession: (id: number) => ["research", "sessions", id] as const,
  researchJobs: (sessionId: number) =>
    ["research", "sessions", sessionId, "jobs"] as const,
  researchJob: (sessionId: number, jobId: string) =>
    ["research", "sessions", sessionId, "jobs", jobId] as const,
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

export function useTriggerWorkerUpdate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.triggerWorkerUpdate(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.workers() });
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

// ─── Parameter Sets ──────────────────────────────────────────────────────────

export function useParameterSets(algorithmId: string) {
  return useQuery({
    queryKey: ["algorithms", algorithmId, "parameter-sets"] as const,
    queryFn: () => api.listParameterSets(algorithmId),
    enabled: !!algorithmId,
  });
}

export function useCreateParameterSet(algorithmId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; config_values: Record<string, unknown> }) =>
      api.createParameterSet(algorithmId, body),
    onSuccess: () => {
      void qc.invalidateQueries({
        queryKey: ["algorithms", algorithmId, "parameter-sets"],
      });
    },
  });
}

export function useUpdateParameterSet(algorithmId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ setId, name }: { setId: string; name: string }) =>
      api.updateParameterSet(algorithmId, setId, { name }),
    onSuccess: () => {
      void qc.invalidateQueries({
        queryKey: ["algorithms", algorithmId, "parameter-sets"],
      });
    },
  });
}

export function useDeleteParameterSet(algorithmId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (setId: string) =>
      api.deleteParameterSet(algorithmId, setId),
    onSuccess: () => {
      void qc.invalidateQueries({
        queryKey: ["algorithms", algorithmId, "parameter-sets"],
      });
    },
  });
}

export function useImportParameterSets(algorithmId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sets: Array<{ name: string; config_values: Record<string, unknown> }>) =>
      api.importParameterSets(algorithmId, sets),
    onSuccess: () => {
      void qc.invalidateQueries({
        queryKey: ["algorithms", algorithmId, "parameter-sets"],
      });
    },
  });
}

// ─── Instances ────────────────────────────────────────────────────────────────

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
    queryFn: () => api.listDownloads(),
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

export function useRetryDownload() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (downloadId: string) => api.retryDownload(downloadId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.downloads() });
    },
  });
}

// ─── Data Goals ───────────────────────────────────────────────────────────────

export function useDataGoals() {
  return useQuery({
    queryKey: ["data", "goals"],
    queryFn: api.listGoals,
    refetchInterval: 5000,
  });
}

export function useCreateGoal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: GoalCreate) => api.createGoal(body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["data", "goals"] }),
  });
}

export function usePauseGoal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.pauseGoal(id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["data", "goals"] }),
  });
}

export function useResumeGoal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.resumeGoal(id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["data", "goals"] }),
  });
}

export function useUpdateGoal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: GoalCreate }) => api.updateGoal(id, body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["data", "goals"] }),
  });
}

export function useDeleteGoal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteGoal(id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["data", "goals"] }),
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

// ─── Coverage ─────────────────────────────────────────────────────────────────

export function useProviders() {
  return useQuery({
    queryKey: ["data", "providers"] as const,
    queryFn: api.listProviderTimeframes,
    staleTime: 60_000,
  });
}

export function useProviderAvailability() {
  return useQuery({
    queryKey: ["data", "providers", "availability"] as const,
    queryFn: api.listProviderAvailability,
    staleTime: 60_000,
  });
}

export function useStorageSummary() {
  return useQuery({
    queryKey: ["data", "storage-summary"] as const,
    queryFn: api.getStorageSummary,
    staleTime: 30_000,
  });
}

export function useCoverage() {
  return useQuery({
    queryKey: ["data", "coverage"] as const,
    queryFn: api.getCoverage,
    staleTime: 30_000,
  });
}

export function useFillGaps() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: FillGapsRequest) => api.fillGaps(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["data", "coverage"] });
      void qc.invalidateQueries({ queryKey: keys.downloads() });
    },
  });
}

export function useDeleteDatasets() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { provider: string; symbol: string; timeframe: string }[]) =>
      api.deleteDatasets(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["data", "coverage"] });
    },
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

export function useSetFmpKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (value: string) => api.setFmpKey(value),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.settings() });
    },
  });
}

export function useDeleteFmpKey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.deleteFmpKey(),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.settings() });
    },
  });
}

export function useSetFmpTier() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      daily_quota_limit?: number | null;
      min_request_interval_s?: number | null;
      quota_reset_tz?: string | null;
    }) => api.setFmpTier(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.settings() });
    },
  });
}

export function useDeleteFmpTier() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.deleteFmpTier(),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.settings() });
    },
  });
}

export function useSetCoordinatorIp() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (value: string) => api.setCoordinatorIp(value),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.settings() });
    },
  });
}

export function useDeleteCoordinatorIp() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.deleteCoordinatorIp(),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.settings() });
    },
  });
}

export function useSetTailscaleAuthkey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (value: string) => api.setTailscaleAuthkey(value),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.settings() });
    },
  });
}

export function useDeleteTailscaleAuthkey() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.deleteTailscaleAuthkey(),
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

// ── U3: close position ──

export function useClosePosition(accountId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof api.closePosition>[1]) =>
      api.closePosition(accountId, body),
    onSuccess: () => {
      // Match the actual query key used by useBrokerInfo so the table refetches.
      void qc.invalidateQueries({
        queryKey: ["accounts", accountId, "broker-info"],
      });
    },
  });
}

// ── U4: close position by ID ──

export function useClosePositionById() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ accountId, positionId, body }: { accountId: string; positionId: string; body?: { order_type?: "market" | "limit" | "stop"; limit_price?: number; stop_price?: number; quantity?: number } }) =>
      api.closePositionById(accountId, positionId, body || {}),
    onSuccess: (_data, { accountId }) => {
      void qc.invalidateQueries({ queryKey: ["accounts", accountId, "broker-info"] });
    },
  });
}

// ── U4: reconcile positions ──

export function useReconcilePositions(accountId: string) {
  return useQuery({
    queryKey: ["accounts", accountId, "positions", "reconcile"] as const,
    queryFn: () => api.reconcilePositions(accountId),
    enabled: !!accountId,
    staleTime: 30_000,
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

export function useUnsubscribeLiveSubscription() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.unsubscribeLiveSubscription(id),
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

/** Fetch market data, allowing `source` (provider or live source like `alpaca_live`).
 *
 * Defaults to `limit=5000` (most-recent bars) to prevent loading hundreds of
 * thousands of rows for high-frequency timeframes.  Pass an explicit `limit` to
 * override; set `limit` to a large number (e.g. 0 never sent, so omit) if you
 * want uncapped data.
 */
export function useMarketDataSource(
  source: string | null,
  symbol: string | null,
  timeframe: string | null,
  bars?: number,
  limit = 5000
) {
  return useQuery({
    queryKey: ["market-data-source", source, symbol, timeframe, bars ?? null, limit] as const,
    queryFn: () =>
      api.getMarketDataWithSource(symbol!, {
        source: source!,
        timeframe: timeframe!,
        ...(bars !== undefined ? { bars } : {}),
        limit,
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

export function useOptionChainMatrix(
  accountId: string,
  underlying: string | null,
  maxExpiries = 60
) {
  return useQuery({
    queryKey: ["option-chain-matrix", accountId, underlying, maxExpiries] as const,
    queryFn: () => api.getOptionChainMatrix(accountId, underlying!, maxExpiries),
    enabled: !!accountId && !!underlying,
    staleTime: 60_000,
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

export function useBacktestReport(
  id: string,
  opts?: { refetchInterval?: number },
) {
  return useQuery({
    queryKey: ["backtest-report", id] as const,
    queryFn: () => api.getBacktestReport(id),
    enabled: !!id,
    refetchInterval: opts?.refetchInterval,
  });
}

export function useBacktestEquityWindow(
  id: string,
  params: { from: string; to: string; resolution?: "1min" | "1hour" | "1day" | "auto" } | null,
) {
  return useQuery({
    queryKey: ["backtest-equity-window", id, params] as const,
    queryFn: () => api.getBacktestEquityWindow(id, params!),
    enabled: !!id && params != null,
    staleTime: 60_000,
    retry: false,  // 404s on missing resolution are expected (pyramid v2)
  });
}

export function useBacktestTrades(
  id: string,
  limit = 500,
  offset = 0,
  opts?: { refetchInterval?: number },
) {
  return useQuery({
    queryKey: ["backtest-trades", id, limit, offset] as const,
    queryFn: () => api.getBacktestTrades(id, { limit, offset }),
    enabled: !!id,
    refetchInterval: opts?.refetchInterval,
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

// ─── Deployments ──────────────────────────────────────────────────────────────

export function useDeployments(params?: { algorithm_id?: string; worker_id?: string; account_id?: string }) {
  return useQuery({
    queryKey: keys.deployments(params),
    queryFn: () => api.listDeployments(params),
  });
}

export function useDeployment(id: string) {
  return useQuery({
    queryKey: keys.deployment(id),
    queryFn: () => api.getDeployment(id),
    enabled: !!id,
  });
}

export function useDeploymentRuns(id: string) {
  return useQuery({
    queryKey: keys.deploymentRuns(id),
    queryFn: () => api.listDeploymentRuns(id),
    enabled: !!id,
  });
}

export function useUpdateDeployment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: { config_values?: Record<string, unknown> } }) =>
      api.updateDeployment(id, body),
    onSuccess: (_data, { id }) => {
      void qc.invalidateQueries({ queryKey: keys.deployment(id) });
      void qc.invalidateQueries({ queryKey: ["deployments"] });
    },
  });
}

export function useDeleteDeployment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.deleteDeployment(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["deployments"] });
    },
  });
}

// ─── M3.3: Start / Stop Deployment (optimistic) ───────────────────────────────

export function useStartDeployment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.startDeployment(id),
    onMutate: async (id) => {
      await qc.cancelQueries({ queryKey: keys.deployment(id) });
      const prev = qc.getQueryData<Deployment>(keys.deployment(id));
      if (prev) {
        qc.setQueryData<Deployment>(keys.deployment(id), { ...prev, status: "starting" });
      }
      return { prev };
    },
    onError: (_err, id, ctx) => {
      if (ctx?.prev) qc.setQueryData(keys.deployment(id), ctx.prev);
    },
    onSettled: (_data, _err, id) => {
      void qc.invalidateQueries({ queryKey: keys.deployment(id) });
      void qc.invalidateQueries({ queryKey: ["deployments"] });
    },
  });
}

export function useStopDeployment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.stopDeployment(id),
    onMutate: async (id) => {
      await qc.cancelQueries({ queryKey: keys.deployment(id) });
      const prev = qc.getQueryData<Deployment>(keys.deployment(id));
      if (prev) {
        qc.setQueryData<Deployment>(keys.deployment(id), { ...prev, status: "stopping" });
      }
      return { prev };
    },
    onError: (_err, id, ctx) => {
      if (ctx?.prev) qc.setQueryData(keys.deployment(id), ctx.prev);
    },
    onSettled: (_data, _err, id) => {
      void qc.invalidateQueries({ queryKey: keys.deployment(id) });
      void qc.invalidateQueries({ queryKey: ["deployments"] });
    },
  });
}

export function useRedeployDeployment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.redeployDeployment(id),
    onSuccess: (_data, id) => {
      void qc.invalidateQueries({ queryKey: keys.deployment(id) });
      void qc.invalidateQueries({ queryKey: ["deployments"] });
    },
  });
}

export function useDeploymentReport(
  id: string,
  opts?: { refetchInterval?: number | false },
) {
  return useQuery({
    queryKey: ["deployment-report", id] as const,
    queryFn: () => api.getDeploymentReport(id),
    enabled: !!id,
    refetchInterval: opts?.refetchInterval,
    retry: false,
  });
}

// ─── M4.5: Activity feeds ─────────────────────────────────────────────────────

type ActivityParams = { limit?: number; before?: string; severity?: string; event_types?: string; kind?: string };

export function useWorkerActivity(workerId: string, params?: ActivityParams) {
  return useQuery({
    queryKey: ["worker-activity", workerId, params] as const,
    queryFn: () => api.listWorkerActivity(workerId, params),
    enabled: !!workerId,
  });
}

export function useDeploymentActivity(deploymentId: string, params?: ActivityParams) {
  return useQuery({
    queryKey: ["deployment-activity", deploymentId, params] as const,
    queryFn: () => api.listDeploymentActivity(deploymentId, params),
    enabled: !!deploymentId,
  });
}

// ── M6.4: Deployment trades ──

export function useDeploymentTrades(
  id: string,
  opts?: { limit?: number; run_id?: string; refetchInterval?: number | false },
) {
  return useQuery({
    queryKey: ["deployment-trades", id, opts?.limit, opts?.run_id] as const,
    queryFn: () => api.listDeploymentTrades(id, { limit: opts?.limit, run_id: opts?.run_id }),
    enabled: !!id,
    refetchInterval: opts?.refetchInterval,
  });
}

// ─── M3.3: WebSocket-driven deployment cache sync ─────────────────────────────

export function useDeploymentStatusSync(): void {
  const qc = useQueryClient();
  useEffect(() => {
    const off = wsManager.subscribe("deployment_status_changed", (data: unknown) => {
      const msg = data as { deployment_id?: string };
      if (!msg.deployment_id) return;
      void qc.invalidateQueries({ queryKey: keys.deployment(msg.deployment_id) });
      void qc.invalidateQueries({ queryKey: ["deployments"] });
      void qc.invalidateQueries({ queryKey: keys.deploymentRuns(msg.deployment_id) });
    });
    return off;
  }, [qc]);
}

// ─── WebSocket topic hook ──────────────────────────────────────────────────────

export function useWebSocketTopic<T = unknown>(topic: string | null): T | null {
  const [latest, setLatest] = useState<T | null>(null);

  useEffect(() => {
    if (!topic) return;

    wsManager.send({ type: "subscribe", target: topic });

    const msgType = topic.startsWith("account:") && topic.endsWith(":setup_progress")
      ? "setup_progress"
      : topic.startsWith("account:")
        ? "account_equity_update"
        : topic === "portfolio:summary"
          ? "portfolio_summary_update"
          : topic;

    const unsub = wsManager.subscribe(msgType, (data: unknown) => {
      setLatest(data as T);
    });

    return () => {
      unsub();
      wsManager.send({ type: "unsubscribe", target: topic });
    };
  }, [topic]);

  return latest;
}
