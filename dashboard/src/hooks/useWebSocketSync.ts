import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { wsManager } from "../api/websocket";
import { keys } from "../api/hooks";

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

    return () => {
      unsubscribeInstanceStarted();
      unsubscribeInstanceStopped();
      unsubscribeInstanceError();
      unsubscribeHeartbeat();
      unsubscribeTradeExecuted();
      unsubscribeStateCheckpoint();
      unsubscribeDeploymentStatus();
      wsManager.disconnect();
    };
  }, [queryClient]);
}
