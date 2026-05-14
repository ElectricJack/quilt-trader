import { useEffect } from "react";
import { wsManager } from "../api/websocket";

export function useWorkerConnectedEvent(
  workerId: string | null,
  onConnected: (msg: unknown) => void
): void {
  useEffect(() => {
    if (!workerId) return;
    const unsubscribe = wsManager.subscribe("worker_connected", (data) => {
      const msg = data as Record<string, unknown>;
      if (msg.worker_id === workerId) {
        onConnected(msg);
      }
    });
    return unsubscribe;
  }, [workerId, onConnected]);
}
