import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { ScraperRecord } from "../api/client";

const handlers = new Map<string, (data: unknown) => void>();

vi.mock("../api/websocket", () => ({
  wsManager: {
    connect: vi.fn(),
    disconnect: vi.fn(),
    subscribe: (event: string, handler: (data: unknown) => void) => {
      handlers.set(event, handler);
      return () => handlers.delete(event);
    },
  },
}));

import { useWebSocketSync } from "./useWebSocketSync";
import { keys } from "../api/hooks";

function record(overrides: Partial<ScraperRecord> = {}): ScraperRecord {
  return {
    name: "alpha-picks-scraper",
    schedule: "0 16 * * 1-5",
    jitter_seconds: null,
    next_run_at: null,
    version: null,
    description: null,
    config_overrides: [],
    last_status: "ok",
    last_run_at: null,
    data_url: "/api/data/custom/alpha-picks-scraper",
    last_error: null,
    auth: { kind: "browser_profile", login_supported: true, headed: true },
    auth_state: "ok",
    auth_reason: null,
    auth_message: null,
    auth_changed_at: null,
    schedule_paused: false,
    login_session: null,
    ...overrides,
  };
}

function setup() {
  const qc = new QueryClient();
  qc.setQueryData(keys.scrapers(), [record(), record({ name: "other" })]);
  const invalidate = vi.spyOn(qc, "invalidateQueries");
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  renderHook(() => useWebSocketSync(), { wrapper });
  return { qc, invalidate };
}

beforeEach(() => handlers.clear());

describe("useWebSocketSync scraper events", () => {
  it("scraper_auth_changed patches auth_state and refetches the scraper list", () => {
    const { qc, invalidate } = setup();
    handlers.get("scraper_auth_changed")!({
      type: "scraper_auth_changed",
      name: "alpha-picks-scraper",
      auth_state: "needs_login",
    });
    const list = qc.getQueryData<ScraperRecord[]>(keys.scrapers())!;
    expect(list[0]).toMatchObject({ auth_state: "needs_login", schedule_paused: true });
    expect(list[1]).toMatchObject({ auth_state: "ok" });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: keys.scrapers() });
  });

  it("scraper_login_state patches the session state and refetches", () => {
    const { qc, invalidate } = setup();
    qc.setQueryData(keys.scrapers(), [
      record({
        login_session: {
          id: "s1",
          state: "waiting_for_user",
          message: null,
          started_at: null,
          expires_at: null,
          can_check: true,
        },
      }),
    ]);
    handlers.get("scraper_login_state")!({
      type: "scraper_login_state",
      name: "alpha-picks-scraper",
      state: "confirming",
    });
    const list = qc.getQueryData<ScraperRecord[]>(keys.scrapers())!;
    expect(list[0].login_session?.state).toBe("confirming");
    expect(invalidate).toHaveBeenCalledWith({ queryKey: keys.scrapers() });
  });

  it("a login state for a scraper without a cached session only refetches", () => {
    const { qc, invalidate } = setup();
    handlers.get("scraper_login_state")!({
      type: "scraper_login_state",
      name: "alpha-picks-scraper",
      state: "starting",
    });
    expect(qc.getQueryData<ScraperRecord[]>(keys.scrapers())![0].login_session).toBeNull();
    expect(invalidate).toHaveBeenCalledWith({ queryKey: keys.scrapers() });
  });
});
