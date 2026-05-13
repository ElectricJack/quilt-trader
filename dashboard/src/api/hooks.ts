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
