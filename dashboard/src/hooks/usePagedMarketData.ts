import { useState, useEffect, useCallback, useRef } from "react";
import { api } from "../api/client";
import type { MarketDataBar } from "../types";

// ─── Internal helpers ─────────────────────────────────────────────────────────

/** Convert a raw API bar to a stable { time (unix seconds), ohlcv } object. */
function parseBar(bar: MarketDataBar): ParsedBar {
  return {
    time: Math.floor(new Date(bar.timestamp).getTime() / 1000),
    open: bar.open,
    high: bar.high,
    low: bar.low,
    close: bar.close,
    volume: bar.volume,
  };
}

/** Merge two bar arrays, deduplicating by unix-second timestamp, sorted ascending. */
function mergeBars(existing: ParsedBar[], incoming: ParsedBar[]): ParsedBar[] {
  const map = new Map<number, ParsedBar>();
  for (const b of existing) map.set(b.time, b);
  for (const b of incoming) map.set(b.time, b);
  const arr = Array.from(map.values());
  arr.sort((a, b) => a.time - b.time);
  return arr;
}

// ─── Types ────────────────────────────────────────────────────────────────────

export interface ParsedBar {
  /** Unix timestamp in seconds (lightweight-charts Time-compatible). */
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface PagedMarketDataResult {
  /** All bars loaded so far, sorted ascending by time. */
  bars: ParsedBar[];
  /** True while the initial page is being fetched. */
  loading: boolean;
  /** True while a background page is being fetched (user panned left). */
  fetchingMore: boolean;
  /**
   * Call when the chart viewport approaches the left edge of loaded data.
   * Pass the ISO timestamp of the earliest bar currently in the viewport.
   * The hook will fetch the page of bars that precede that timestamp.
   */
  loadEarlier: (beforeTimestamp: string) => void;
  /** True when we've loaded all available history (no more pages to fetch). */
  reachedStart: boolean;
}

// ─── Hook ─────────────────────────────────────────────────────────────────────

/**
 * Manages a paged market-data buffer for a single (source, symbol, timeframe)
 * combination.
 *
 * - Loads the most recent 5 000 bars on mount.
 * - Fetches an earlier page whenever `loadEarlier` is called with the oldest
 *   timestamp currently visible on the chart.
 * - Deduplicates and sorts bars on each merge so the chart always receives a
 *   clean ascending series.
 */
export function usePagedMarketData(
  source: string | null,
  symbol: string | null,
  timeframe: string | null,
): PagedMarketDataResult {
  const [bars, setBars] = useState<ParsedBar[]>([]);
  const [loading, setLoading] = useState(false);
  const [fetchingMore, setFetchingMore] = useState(false);
  const [reachedStart, setReachedStart] = useState(false);

  // Tracks which "before:<iso>" keys we have already requested so we never
  // fire duplicate requests for the same page boundary.
  const fetchedRangesRef = useRef<Set<string>>(new Set());
  // The earliest timestamp available on the server for this dataset.
  const serverMinRef = useRef<string | null>(null);
  // Guard against concurrent background fetches (the ref is checked
  // synchronously, unlike the `fetchingMore` state value).
  const fetchingMoreRef = useRef(false);

  // ── Initial load ──────────────────────────────────────────────────────────

  useEffect(() => {
    if (!source || !symbol || !timeframe) {
      setBars([]);
      setLoading(false);
      setFetchingMore(false);
      setReachedStart(false);
      return;
    }

    // Reset all state when the dataset identity changes.
    fetchedRangesRef.current = new Set();
    serverMinRef.current = null;
    fetchingMoreRef.current = false;
    setBars([]);
    setReachedStart(false);
    setLoading(true);

    let cancelled = false;

    // Fire both requests in parallel: meta (for early-exit guard) + first page.
    api
      .getMarketDataMeta(symbol, { source, timeframe })
      .then((meta) => {
        if (cancelled) return;
        serverMinRef.current = meta.first_timestamp;
      })
      .catch(() => {
        // Meta is advisory — don't block the UI if it fails.
      });

    api
      .getMarketDataPaged(symbol, { source, timeframe, limit: 5000 })
      .then((res) => {
        if (cancelled) return;
        const parsed = (res.data ?? []).map(parseBar);
        setBars(parsed);
      })
      .catch(() => {
        if (!cancelled) setBars([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [source, symbol, timeframe]);

  // ── Load earlier page ─────────────────────────────────────────────────────

  const loadEarlier = useCallback(
    (beforeTimestamp: string) => {
      if (!source || !symbol || !timeframe) return;
      if (fetchingMoreRef.current) return;
      if (reachedStart) return;

      // If the server told us its earliest timestamp and we're already at or
      // before it, there's nothing older to fetch.
      if (serverMinRef.current && beforeTimestamp <= serverMinRef.current) {
        setReachedStart(true);
        return;
      }

      // De-duplicate requests for the same page boundary.
      const key = `before:${beforeTimestamp}`;
      if (fetchedRangesRef.current.has(key)) return;
      fetchedRangesRef.current.add(key);

      fetchingMoreRef.current = true;
      setFetchingMore(true);

      api
        .getMarketDataPaged(symbol, {
          source,
          timeframe,
          end: beforeTimestamp,
          limit: 5000,
        })
        .then((res) => {
          const newBars = (res.data ?? []).map(parseBar);
          if (newBars.length === 0) {
            setReachedStart(true);
          } else {
            setBars((prev) => mergeBars(prev, newBars));
          }
        })
        .catch(() => {
          // Remove the key so the user can retry by panning again.
          fetchedRangesRef.current.delete(key);
        })
        .finally(() => {
          fetchingMoreRef.current = false;
          setFetchingMore(false);
        });
    },
    // reachedStart is included so the guard re-evaluates once set.
    [source, symbol, timeframe, reachedStart],
  );

  return { bars, loading, fetchingMore, loadEarlier, reachedStart };
}
