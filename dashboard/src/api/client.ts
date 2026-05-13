import type {
  Account,
  Algorithm,
  AlgorithmInstance,
  InstalledAlgorithmResponse,
  Worker,
  SystemEvent,
  PaginatedResponse,
  RepoInfo,
  SettingsStatus,
  HealthResponse,
} from "../types";

// ─── Request body types ────────────────────────────────────────────────────────

export interface AccountCreate {
  name: string;
  broker_type: string;
  credentials: Record<string, unknown>;
  supported_asset_types: string[];
  options_level?: number;
  account_features?: string[];
  pdt_mode?: string;
}

export interface AccountUpdate {
  name?: string;
  credentials?: Record<string, unknown>;
  supported_asset_types?: string[];
  options_level?: number;
  account_features?: string[];
  pdt_mode?: string;
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
};
