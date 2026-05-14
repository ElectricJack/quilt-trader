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
  useOptionExpiries,
  useOptionChain,
  useOpenPosition,
} from "../api/hooks";
import { useUIStore } from "../stores/ui";
import type { OptionLeg } from "../lib/options";
import {
  buildTemplate,
  TEMPLATE_LABELS,
  type TemplateName,
} from "../components/strategy/templates";
import { usePersistedBuilderState } from "../components/strategy/usePersistedBuilderState";
import { LegsTable } from "../components/strategy/LegsTable";
import { ChainBrowser } from "../components/strategy/ChainBrowser";
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

  const { data: expData } = useOptionExpiries(
    accountId,
    underlying.trim() ? underlying.trim() : null
  );
  const { data: chain, isLoading: chainLoading } = useOptionChain(
    accountId,
    underlying.trim() ? underlying.trim() : null,
    expiry
  );

  // When the chain loads, refresh bid/ask/iv on any leg that matches a
  // contract in the new chain. Structural fields (side/right/strike/expiry)
  // are left alone — those came from the persisted state.
  useEffect(() => {
    if (!chain) return;
    setLegs((prev) =>
      prev.map((l) => {
        if (l.expiry !== chain.expiry) return l;
        const match = chain.contracts.find(
          (c) => c.right === l.right && c.strike === l.strike
        );
        if (!match) return l;
        return {
          ...l,
          bid: match.bid ?? undefined,
          ask: match.ask ?? undefined,
          iv: match.iv ?? l.iv,
        };
      })
    );
  }, [chain]);

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
        <label className="text-xs text-gray-400">
          Expiry
          <select
            value={expiry ?? ""}
            onChange={(e) => setExpiry(e.target.value || null)}
            disabled={!expData?.expiries?.length}
            className="ml-2 bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-100 disabled:opacity-50"
          >
            <option value="">
              {expData?.expiries?.length ? "Select expiry" : "—"}
            </option>
            {(expData?.expiries ?? []).map((d: string) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="space-y-4">
          <LegsTable legs={legs} onChange={setLegs} chain={chain} expiry={expiry} />
          <ChainBrowser
            chain={chain}
            expiry={expiry}
            onPick={handlePickFromChain}
            isLoading={chainLoading}
          />
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
