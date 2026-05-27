// dashboard/src/pages/Strategies.tsx (page title: "Open Position")
// Unified order-placement page. Routed at /accounts/:id/open-position
// (and /accounts/:id/strategies for back-compat).
//
// Asset-class tabs at the top adapt the body:
//   - Equities / Crypto → SimpleOrderForm (single-leg, single broker ticket)
//   - Options           → full chain matrix + templates + multi-leg builder
//                         (broker-atomic multi-leg via submit_multileg_order)
//
// Equities/crypto are restricted to a single leg deliberately: the
// broker layer doesn't support atomic multi-leg execution for them,
// so multi-leg would risk partial fills with no automatic rollback.

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
import { SimpleOrderForm } from "../components/strategy/SimpleOrderForm";

type AssetClass = "equities" | "crypto" | "options";

const ASSET_CLASS_LABELS: Record<AssetClass, string> = {
  equities: "Equities",
  crypto: "Crypto",
  options: "Options",
};

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

  // Account-derived list of asset classes the user can pick.
  const supportedAssetClasses = useMemo<AssetClass[]>(() => {
    const allowed = new Set(account?.supported_asset_types ?? []);
    return (["equities", "crypto", "options"] as AssetClass[])
      .filter((c) => allowed.has(c));
  }, [account]);

  const [assetClass, setAssetClass] = useState<AssetClass>("equities");
  // Re-anchor the active tab to one the account actually supports.
  useEffect(() => {
    if (supportedAssetClasses.length === 0) return;
    if (!supportedAssetClasses.includes(assetClass)) {
      setAssetClass(supportedAssetClasses[0]);
    }
  }, [supportedAssetClasses, assetClass]);

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

  // Re-anchor the current template at a clicked strike + expiry.
  function handleAnchor(strike: number, exp: string, _right: "call" | "put") {
    if (template === "custom") return;
    setExpiry(exp);
    if (!matrix) return;
    const slice = matrix.expiries.find((e) => e.expiry === exp);
    if (!slice) return;
    const syntheticChain: OptionChainResponse = {
      underlying: matrix.underlying,
      spot: matrix.spot ?? strike,
      expiry: exp,
      as_of: null,
      contracts: slice.contracts,
    };
    const built = buildTemplate(template, syntheticChain, exp, strike);
    if (built.length === 0) {
      addAlert({
        message: `Template "${template}" couldn't be built at ${strike} / ${exp}`,
        severity: "warning",
      });
      return;
    }
    setLegs(built);
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

  function handleAfterSimpleSubmit() {
    addAlert({ message: "Order submitted", severity: "success" });
    // Don't navigate away — let the user place another quickly.
  }

  // Wire mutation error toasts for the simple form (options path
  // surfaces its own via handleSubmit).
  useEffect(() => {
    if (submitMut.isError && submitMut.error) {
      addAlert({
        message: `Order failed: ${submitMut.error.message}`,
        severity: "error",
      });
      submitMut.reset();
    }
  }, [submitMut.isError, submitMut.error, submitMut, addAlert]);

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
          <h1 className="text-xl font-bold">Open Position — {account.name}</h1>
        </div>
        {assetClass === "options" && (
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
        )}
      </div>

      {/* Asset-class segmented control */}
      {supportedAssetClasses.length > 0 && (
        <div className="inline-flex rounded-lg border border-gray-700 bg-gray-900 p-0.5">
          {supportedAssetClasses.map((c) => (
            <button
              key={c}
              onClick={() => setAssetClass(c)}
              className={`px-4 py-1.5 text-sm rounded-md transition-colors ${
                assetClass === c
                  ? "bg-indigo-600 text-white"
                  : "text-gray-400 hover:text-gray-200"
              }`}
            >
              {ASSET_CLASS_LABELS[c]}
            </button>
          ))}
        </div>
      )}

      {assetClass !== "options" ? (
        <SimpleOrderForm
          assetType={assetClass}
          submitMut={submitMut}
          onAfterSubmit={handleAfterSimpleSubmit}
        />
      ) : (
      <>
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
        template={template}
        legs={legs}
        onPick={handlePickFromChain}
        onAnchor={handleAnchor}
      />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="space-y-4">
          <LegsTable
            legs={legs}
            onChange={setLegs}
            chain={chain}
            expiry={expiry}
            underlying={underlying.trim() || undefined}
          />
        </div>
        <div className="space-y-4">
          <PnlChart legs={legs} spot={chain?.spot} scrubMs={scrubMs} />
          <DateSlider legs={legs} valueMs={scrubMs} onChange={setScrubMs} />
          <GreeksPanel legs={legs} spot={chain?.spot} scrubMs={scrubMs} />
        </div>
      </div>
      </>
      )}
    </div>
  );
}
