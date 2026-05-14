// dashboard/src/components/strategy/usePersistedBuilderState.ts
// Per-account localStorage persistence for the strategy builder.
//
// Stores only the *structural* fields of each leg (side, right, strike,
// expiry, quantity). bid/ask/iv must be re-hydrated from the chain on load.
//
// Self-pruning behaviour:
//   - Entries older than 30 days are auto-removed on read.
//   - Legs whose expiry has passed are dropped on hydrate, and the consumer
//     is notified via `needsToastMsg` so it can surface a warning.

import { useEffect, useRef, useState } from "react";
import type { OptionLeg } from "../../lib/options";

const STORAGE_VERSION = 1;
const STORAGE_KEY = (accountId: string) => `quilt.strategyBuilder.${accountId}`;
const MAX_AGE_MS = 30 * 24 * 3600 * 1000;
const DEBOUNCE_MS = 500;

export type StructuralLeg = Pick<
  OptionLeg,
  "side" | "right" | "strike" | "expiry" | "quantity"
>;

export type PersistedBuilderState = {
  version: 1;
  underlying: string | null;
  template: string | null;
  legs: StructuralLeg[];
  scrubDateOffsetMs: number | null;
  expiry: string | null;
  savedAt: number;
};

export type PersistedSavePayload = Omit<PersistedBuilderState, "version" | "savedAt">;

export function usePersistedBuilderState(accountId: string) {
  const [hydrated, setHydrated] = useState<PersistedBuilderState | null>(null);
  const [needsToastMsg, setNeedsToastMsg] = useState<string | null>(null);
  const debounceRef = useRef<number | null>(null);

  useEffect(() => {
    if (!accountId) return;
    try {
      const raw = localStorage.getItem(STORAGE_KEY(accountId));
      if (!raw) return;
      const parsed = JSON.parse(raw) as PersistedBuilderState;
      if (parsed.version !== STORAGE_VERSION) return;
      if (Date.now() - parsed.savedAt > MAX_AGE_MS) {
        localStorage.removeItem(STORAGE_KEY(accountId));
        return;
      }
      const today = new Date().toISOString().slice(0, 10);
      const validLegs = (parsed.legs ?? []).filter((l) => l.expiry >= today);
      const droppedCount = (parsed.legs ?? []).length - validLegs.length;
      if (droppedCount > 0) {
        setNeedsToastMsg(
          `Removed ${droppedCount} expired leg${droppedCount === 1 ? "" : "s"}`
        );
      }
      setHydrated({ ...parsed, legs: validLegs });
    } catch {
      // corrupt; ignore
    }
  }, [accountId]);

  function save(state: PersistedSavePayload) {
    if (!accountId) return;
    if (debounceRef.current != null) {
      window.clearTimeout(debounceRef.current);
    }
    debounceRef.current = window.setTimeout(() => {
      const payload: PersistedBuilderState = {
        ...state,
        version: 1,
        savedAt: Date.now(),
      };
      try {
        localStorage.setItem(STORAGE_KEY(accountId), JSON.stringify(payload));
      } catch {
        // quota / blocked storage; ignore
      }
    }, DEBOUNCE_MS);
  }

  function reset() {
    if (!accountId) return;
    if (debounceRef.current != null) {
      window.clearTimeout(debounceRef.current);
      debounceRef.current = null;
    }
    try {
      localStorage.removeItem(STORAGE_KEY(accountId));
    } catch {
      // ignore
    }
  }

  return { hydrated, needsToastMsg, save, reset };
}
