// dashboard/src/pages/Strategies.tsx
// Options strategy builder. Account-bound at /accounts/:id/strategies.
// Pulls option expiries/chain from the broker, builds + edits leg lists
// (templates + manual), shows client-side Black-Scholes P&L + Greeks, and
// submits the constructed spread via Spec A's positions/open endpoint.

import { useEffect, useMemo, useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { ChevronLeft, RotateCcw } from "lucide-react";
import {
  useAccount,
  useOptionChainMatrix,
  useOpenPosition,
} from "../api/hooks";
import { useUIStore } from "../stores/ui";
import type { OptionLeg } from "../lib/options";
import type { OptionChainResponse } from "../api/client";
import {
  buildTemplate,
  TEMPLATE_LABELS,
  type TemplateName,
} from "../components/strategy/templates";
import { usePersistedBuilderState } from "../components/strategy/usePersistedBuilderState";
import { LegsTable } from "../components/strategy/LegsTable";
import { StrategyChainMatrix } from "../components/strategy/StrategyChainMatrix";
import { PnlChart } from "../components/strategy/PnlChart";
import { DateSlider } from "../components/strategy/DateSlider";
import { GreeksPanel } from "../components/strategy/GreeksPanel";

function legMidPrice(l: OptionLeg): number {
  if (l.bid != null && l.ask != null) return (l.bid + l.ask) / 2;
  if (l.ask != null) return l.ask;
  if (l.bid != null) return l.bid;
  return 0;
}

export function Strategies() {
  const { id } = useParams<{ id: string }>();
  const accountId = id ?? "";
  const navigate = useNavigate();
  const addAlert = useUIStore((s) => s.addAlert);

  const { data: account } = useAccount(accountId);

  const [underlying, setUnderlying] = useState<string>("");
  const [expiry, setExpiry] = useState<string | null>(null);
  const [template, setTemplate] = useState<TemplateName>("custom");
  const [legs, setLegs] = useState<OptionLeg[]>([]);
  const [scrubMs, setScrubMs] = useState<number>(() => Date.now());

  const { hydrated, needsToastMsg, save, reset } = usePersistedBuilderState(accountId);

  // Hydrate persisted state once on mount. bid/ask/iv are NOT persisted —
  // they get re-bound when the chain loads (see effect below).
  useEffect(() => {
    if (!hydrated) return;
    if (hydrated.underlying) setUnderlying(hydrated.underlying);
    if (hydrated.template) setTemplate(hydrated.template as TemplateName);
    if (hydrated.expiry) setExpiry(hydrated.expiry);
    if (hydrated.scrubDateOffsetMs != null) setScrubMs(hydrated.scrubDateOffsetMs);
    setLegs(
      hydrated.legs.map((l) => ({
        ...l,
        bid: undefined,
        ask: undefined,
        iv: 0.3,
      }))
    );
  }, [hydrated]);

  useEffect(() => {
    if (needsToastMsg) addAlert({ message: needsToastMsg, severity: "warning" });
  }, [needsToastMsg, addAlert]);

  const { data: matrix, isLoading: matrixLoading } = useOptionChainMatrix(
    accountId,
    underlying.trim() ? underlying.trim() : null
  );

  // Build a synthetic single-expiry chain for the template builder + greeks.
  // Picks the active expiry (or falls back to the nearest available).
  const chain: OptionChainResponse | undefined = useMemo(() => {
    if (!matrix || matrix.expiries.length === 0) return undefined;
    const targetExp = expiry && matrix.expiries.find((e) => e.expiry === expiry)
      ? expiry
      : matrix.expiries[0].expiry;
    const slice = matrix.expiries.find((e) => e.expiry === targetExp)!;
    return {
      underlying: matrix.underlying,
      spot: matrix.spot ?? 0,
      expiry: slice.expiry,
      as_of: null,
      contracts: slice.contracts,
    };
  }, [matrix, expiry]);

  // Refresh bid/ask/iv on any leg whose (expiry, right, strike) appears in
  // the matrix. Structural fields are left alone — they came from
  // persisted state or template builder.
  useEffect(() => {
    if (!matrix) return;
    const byKey = new Map<string, { bid: number | null; ask: number | null; iv: number | null }>();
    for (const e of matrix.expiries) {
      for (const c of e.contracts) {
        byKey.set(`${e.expiry}|${c.right}|${c.strike}`, {
          bid: c.bid, ask: c.ask, iv: c.iv,
        });
      }
    }
    setLegs((prev) =>
      prev.map((l) => {
        const m = byKey.get(`${l.expiry}|${l.right}|${l.strike}`);
        if (!m) return l;
        return {
          ...l,
          bid: m.bid ?? undefined,
          ask: m.ask ?? undefined,
          iv: m.iv ?? l.iv,
        };
      })
    );
  }, [matrix]);

  // Persist the full builder state on every change (debounced inside the hook).
  useEffect(() => {
    if (!accountId) return;
    save({
      underlying: underlying || null,
      template,
      legs: legs.map((l) => ({
        side: l.side,
        right: l.right,
        strike: l.strike,
        expiry: l.expiry,
        quantity: l.quantity,
      })),
      scrubDateOffsetMs: scrubMs,
      expiry,
    });
  }, [accountId, underlying, template, legs, scrubMs, expiry, save]);

  function applyTemplate(name: TemplateName) {
    setTemplate(name);
    if (name === "custom") return;
    if (chain && expiry) {
      const built = buildTemplate(name, chain, expiry);
      if (built.length === 0) {
        addAlert({
          message: `Template "${name}" couldn't be built from the current chain`,
          severity: "warning",
        });
      } else {
        setLegs(built);
      }
    } else {
      addAlert({
        message: "Pick an underlying + expiry before applying a template",
        severity: "info",
      });
    }
  }

  function handleReset() {
    reset();
    setLegs([]);
    setTemplate("custom");
    setScrubMs(Date.now());
  }

  function handlePickFromChain(leg: OptionLeg) {
    setLegs((prev) => [...prev, leg]);
    setTemplate("custom");
    // Remember which expiry the user is exploring — used as the
    // template-builder target if they apply a template afterwards.
    setExpiry(leg.expiry);
  }

  const submitMut = useOpenPosition(accountId);

  const netCost = useMemo(() => {
    return legs.reduce((sum, l) => {
      const mid = legMidPrice(l);
      return sum + mid * l.quantity * (l.side === "buy" ? 1 : -1);
    }, 0);
  }, [legs]);

  async function handleSubmit() {
    if (!legs.length || !underlying.trim()) return;
    try {
      const result = await submitMut.mutateAsync({
        legs: legs.map((l) => ({
          symbol: underlying.trim(),
          asset_type: "options",
          side: l.side,
          quantity: l.quantity,
          expiry: l.expiry,
          strike: l.strike,
          right: l.right,
        })),
        strategy_type: template,
        order_type: "limit",
        limit_price: Math.round(netCost * 100) / 100,
      });
      if (result.partial_fill) {
        addAlert({
          message: "Order partially filled — check positions",
          severity: "warning",
        });
      } else {
        addAlert({ message: "Order submitted", severity: "success" });
      }
      navigate(`/accounts/${accountId}`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Submit failed";
      addAlert({ message: `Order failed: ${msg}`, severity: "error" });
    }
  }

  if (!accountId) {
    return <p className="text-gray-400">Missing account id.</p>;
  }
  if (!account) {
    return <p className="text-gray-400">Loading…</p>;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link
            to={`/accounts/${accountId}`}
            className="text-gray-400 hover:text-white"
            aria-label="Back to account"
          >
            <ChevronLeft size={20} />
          </Link>
          <h1 className="text-xl font-bold">Strategies — {account.name}</h1>
        </div>
        <div className="flex gap-2">
          <button
            onClick={handleReset}
            className="flex items-center gap-1 px-3 py-1.5 rounded text-sm text-gray-300 bg-gray-700 hover:bg-gray-600"
          >
            <RotateCcw size={14} /> Reset
          </button>
          <button
            onClick={handleSubmit}
            disabled={legs.length === 0 || submitMut.isPending || !underlying.trim()}
            className="px-3 py-1.5 rounded text-sm text-white bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {submitMut.isPending ? "Submitting…" : "Submit Order"}
          </button>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <label className="text-xs text-gray-400">
          Underlying
          <input
            value={underlying}
            onChange={(e) => setUnderlying(e.target.value.toUpperCase())}
            placeholder="e.g. SPY"
            className="ml-2 bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm w-32 text-gray-100"
          />
        </label>
        <label className="text-xs text-gray-400">
          Template
          <select
            value={template}
            onChange={(e) => applyTemplate(e.target.value as TemplateName)}
            className="ml-2 bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-100"
          >
            {TEMPLATE_LABELS.map((t) => (
              <option key={t.name} value={t.name}>
                {t.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <StrategyChainMatrix
        matrix={matrix}
        isLoading={matrixLoading}
        onPick={handlePickFromChain}
      />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="space-y-4">
          <LegsTable legs={legs} onChange={setLegs} chain={chain} expiry={expiry} />
        </div>
        <div className="space-y-4">
          <PnlChart legs={legs} spot={chain?.spot} scrubMs={scrubMs} />
          <DateSlider legs={legs} valueMs={scrubMs} onChange={setScrubMs} />
          <GreeksPanel legs={legs} spot={chain?.spot} scrubMs={scrubMs} />
        </div>
      </div>
    </div>
  );
}
