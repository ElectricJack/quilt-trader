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

    return () => {
      unsubscribeInstanceStarted();
      unsubscribeInstanceStopped();
      unsubscribeInstanceError();
      unsubscribeHeartbeat();
      unsubscribeTradeExecuted();
      unsubscribeStateCheckpoint();
      wsManager.disconnect();
    };
  }, [queryClient]);
}
