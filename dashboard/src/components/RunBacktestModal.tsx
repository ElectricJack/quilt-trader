import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useCreateBacktestRun } from "../api/hooks";
import { useUIStore } from "../stores/ui";

// ── Spec D U1: run backtest modal ──

const FEE_PRESETS = {
  none: { buy: [], sell: [] },
  "alpaca-equities": { buy: [], sell: [] },
  "tradier-options": {
    buy:  [{ flat_fee: 0.35, percent_fee: 0.0, maker: true as const, taker: true as const }],
    sell: [{ flat_fee: 0.35, percent_fee: 0.0, maker: true as const, taker: true as const }],
  },
} as const satisfies Record<string, {
  buy: Array<{ flat_fee: number; percent_fee: number; maker: boolean; taker: boolean }>;
  sell: Array<{ flat_fee: number; percent_fee: number; maker: boolean; taker: boolean }>;
}>;

interface Props {
  open: boolean;
  onClose: () => void;
  algorithmId: string;
  manifestConfig?: Array<{ name: string; type: string; default?: unknown }>;
}

export function RunBacktestModal({ open, onClose, algorithmId, manifestConfig = [] }: Props) {
  const navigate = useNavigate();
  const addAlert = useUIStore((s) => s.addAlert);
  const create = useCreateBacktestRun();

  const today = new Date();
  const oneYearAgo = new Date(today.getFullYear() - 1, today.getMonth(), today.getDate());

  const [start, setStart] = useState(oneYearAgo.toISOString().slice(0, 10));
  const [end, setEnd] = useState(today.toISOString().slice(0, 10));
  const [cash, setCash] = useState(100_000);
  const [preset, setPreset] = useState<keyof typeof FEE_PRESETS>("none");
  const [marketBps, setMarketBps] = useState(5.0);
  const [useBarRange, setUseBarRange] = useState(false);
  const [benchmarkSymbol, setBenchmarkSymbol] = useState("SPY");
  const [benchmarkSource, setBenchmarkSource] = useState("polygon");
  const [configOverrides, setConfigOverrides] = useState<Record<string, unknown>>(
    Object.fromEntries(manifestConfig.map((p) => [p.name, p.default]))
  );

  if (!open) return null;

  async function submit() {
    try {
      const fees = FEE_PRESETS[preset];
      const result = await create.mutateAsync({
        algorithm_id: algorithmId,
        date_range_start: new Date(start).toISOString(),
        date_range_end: new Date(end).toISOString(),
        initial_cash: cash,
        config_overrides: Object.keys(configOverrides).length > 0 ? configOverrides : undefined,
        buy_trading_fees: fees.buy.length > 0 ? [...fees.buy] : undefined,
        sell_trading_fees: fees.sell.length > 0 ? [...fees.sell] : undefined,
        slippage_model: {
          market_bps: marketBps,
          limit_bps: 0,
          use_bar_range: useBarRange,
          volume_impact_bps_per_pct: 0,
        },
        benchmark_symbol: benchmarkSymbol || undefined,
        benchmark_source: benchmarkSource || undefined,
      });
      addAlert({ message: `Backtest queued: ${result.id.slice(0, 8)}…`, severity: "info" });
      onClose();
      navigate(`/backtest-runs/${result.id}`);
    } catch (e) {
      addAlert({
        message: `Failed to start backtest: ${(e as Error).message}`,
        severity: "error",
      });
    }
  }

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
      <div className="bg-gray-900 border border-gray-700 rounded-lg p-5 w-full max-w-2xl space-y-3">
        <h2 className="text-lg font-semibold">Run Backtest</h2>

        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm">
            Start date
            <input
              type="date"
              value={start}
              onChange={(e) => setStart(e.target.value)}
              className="block w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm mt-1"
            />
          </label>
          <label className="text-sm">
            End date
            <input
              type="date"
              value={end}
              onChange={(e) => setEnd(e.target.value)}
              className="block w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm mt-1"
            />
          </label>
        </div>

        <label className="text-sm block">
          Initial cash
          <input
            type="number"
            value={cash}
            onChange={(e) => setCash(Number(e.target.value))}
            className="block w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm mt-1"
          />
        </label>

        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm">
            Fee preset
            <select
              value={preset}
              onChange={(e) => setPreset(e.target.value as keyof typeof FEE_PRESETS)}
              className="block w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm mt-1"
            >
              <option value="none">None (no fees)</option>
              <option value="alpaca-equities">Alpaca equities ($0)</option>
              <option value="tradier-options">Tradier options ($0.35/contract)</option>
            </select>
          </label>
          <label className="text-sm">
            Market slippage (bps)
            <input
              type="number"
              step="0.5"
              value={marketBps}
              onChange={(e) => setMarketBps(Number(e.target.value))}
              className="block w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm mt-1"
            />
          </label>
        </div>

        <label className="inline-flex items-center gap-2 text-sm cursor-pointer">
          <input
            type="checkbox"
            checked={useBarRange}
            onChange={(e) => setUseBarRange(e.target.checked)}
          />
          Use bar range for slippage (random fill within next bar&apos;s [low, high])
        </label>

        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm">
            Benchmark symbol
            <input
              value={benchmarkSymbol}
              onChange={(e) => setBenchmarkSymbol(e.target.value.toUpperCase())}
              className="block w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm mt-1"
            />
          </label>
          <label className="text-sm">
            Benchmark source
            <select
              value={benchmarkSource}
              onChange={(e) => setBenchmarkSource(e.target.value)}
              className="block w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm mt-1"
            >
              <option value="polygon">polygon</option>
              <option value="theta">theta</option>
            </select>
          </label>
        </div>

        {manifestConfig.length > 0 && (
          <details className="text-sm">
            <summary className="cursor-pointer text-gray-400">Algorithm config overrides</summary>
            <div className="space-y-2 mt-2">
              {manifestConfig.map((p) => (
                <label key={p.name} className="block text-xs">
                  {p.name} ({p.type})
                  <input
                    value={String(configOverrides[p.name] ?? "")}
                    onChange={(e) =>
                      setConfigOverrides({ ...configOverrides, [p.name]: e.target.value })
                    }
                    className="block w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm mt-1"
                  />
                </label>
              ))}
            </div>
          </details>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <button
            onClick={onClose}
            className="px-3 py-1.5 rounded text-sm text-gray-300 bg-gray-700 hover:bg-gray-600"
          >
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={create.isPending}
            className="px-3 py-1.5 rounded text-sm text-white bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50"
          >
            {create.isPending ? "Starting…" : "Run Backtest"}
          </button>
        </div>
      </div>
    </div>
  );
}
