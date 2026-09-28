import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { wsManager } from "../api/websocket";
import { keys } from "../api/hooks";
import type { ResearchJob, ScraperLoginState, ScraperRecord } from "../api/client";

export function useWebSocketSync(): void {
  const queryClient = useQueryClient();

  useEffect(() => {
    wsManager.connect();

    const unsubscribeInstanceStarted = wsManager.subscribe(
      "instance_started",
      (data) => {
        const payload = data as Record<string, unknown>;
        void queryClient.invalidateQueries({ queryKey: keys.allInstances() });
        if (typeof payload.instance_id === "string") {
          void queryClient.invalidateQueries({
            queryKey: keys.instance(payload.instance_id),
          });
        }
      }
    );

    const unsubscribeInstanceStopped = wsManager.subscribe(
      "instance_stopped",
      (data) => {
        const payload = data as Record<string, unknown>;
        void queryClient.invalidateQueries({ queryKey: keys.allInstances() });
        if (typeof payload.instance_id === "string") {
          void queryClient.invalidateQueries({
            queryKey: keys.instance(payload.instance_id),
          });
        }
      }
    );

    const unsubscribeInstanceError = wsManager.subscribe(
      "instance_error",
      (data) => {
        const payload = data as Record<string, unknown>;
        void queryClient.invalidateQueries({ queryKey: keys.allInstances() });
        if (typeof payload.instance_id === "string") {
          void queryClient.invalidateQueries({
            queryKey: keys.instance(payload.instance_id),
          });
        }
      }
    );

    const unsubscribeHeartbeat = wsManager.subscribe("heartbeat", () => {
      void queryClient.invalidateQueries({ queryKey: keys.workers() });
    });

    const unsubscribeTradeExecuted = wsManager.subscribe(
      "trade_executed",
      () => {
        void queryClient.invalidateQueries({ queryKey: keys.allInstances() });
      }
    );

    const unsubscribeStateCheckpoint = wsManager.subscribe(
      "state_checkpoint",
      () => {
        void queryClient.invalidateQueries({ queryKey: keys.allInstances() });
      }
    );

    // ── M3.3: deployment status sync ──
    const unsubscribeDeploymentStatus = wsManager.subscribe(
      "deployment_status_changed",
      (data) => {
        const payload = data as { deployment_id?: string };
        if (!payload.deployment_id) return;
        void queryClient.invalidateQueries({ queryKey: keys.deployment(payload.deployment_id) });
        void queryClient.invalidateQueries({ queryKey: ["deployments"] });
        void queryClient.invalidateQueries({ queryKey: keys.deploymentRuns(payload.deployment_id) });
      }
    );

    const unsubscribeResearchJob = wsManager.subscribe(
      "research_job",
      (data) => {
        const msg = data as Partial<ResearchJob> & {
          session_id: number;
          job_id: string;
        };
        // Patch any cached jobs-list query for this session
        queryClient.setQueriesData<ResearchJob[] | undefined>(
          { queryKey: keys.researchJobs(msg.session_id) },
          (old) =>
            old?.map((j) =>
              j.job_id === msg.job_id ? { ...j, ...msg } : j,
            ) ?? old,
        );
        // Patch the single-job query if anyone's watching it
        queryClient.setQueryData(
          keys.researchJob(msg.session_id, msg.job_id),
          (old: ResearchJob | undefined) =>
            old ? { ...old, ...msg } : (msg as ResearchJob),
        );
      },
    );

    // ── Scraper re-login (review rev-nimble-bridge 6.3, 7.3) ──
    // Patch the cached list so the banner reacts at once, then refetch for
    // the full record (auth_reason, message, login_session).
    const patchScraper = (name: unknown, patch: (s: ScraperRecord) => ScraperRecord) => {
      if (typeof name !== "string") return;
      queryClient.setQueryData<ScraperRecord[] | undefined>(keys.scrapers(), (old) =>
        old?.map((s) => (s.name === name ? patch(s) : s)),
      );
      void queryClient.invalidateQueries({ queryKey: keys.scrapers() });
    };

    const unsubscribeScraperAuth = wsManager.subscribe(
      "scraper_auth_changed",
      (data) => {
        const msg = data as { name?: unknown; auth_state?: unknown };
        patchScraper(msg.name, (s) =>
          msg.auth_state === "ok" || msg.auth_state === "needs_login"
            ? { ...s, auth_state: msg.auth_state, schedule_paused: msg.auth_state === "needs_login" }
            : s,
        );
      },
    );

    const unsubscribeScraperLogin = wsManager.subscribe(
      "scraper_login_state",
      (data) => {
        const msg = data as { name?: unknown; state?: unknown };
        patchScraper(msg.name, (s) =>
          s.login_session && typeof msg.state === "string"
            ? { ...s, login_session: { ...s.login_session, state: msg.state as ScraperLoginState } }
            : s,
        );
      },
    );

    return () => {
      unsubscribeScraperAuth();
      unsubscribeScraperLogin();
      unsubscribeInstanceStarted();
      unsubscribeInstanceStopped();
      unsubscribeInstanceError();
      unsubscribeHeartbeat();
      unsubscribeTradeExecuted();
      unsubscribeStateCheckpoint();
      unsubscribeDeploymentStatus();
      unsubscribeResearchJob();
      wsManager.disconnect();
    };
  }, [queryClient]);
}
