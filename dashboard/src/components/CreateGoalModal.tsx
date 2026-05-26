import { useState } from "react";
import type { GoalCreate } from "../api/client";

interface Props {
  open: boolean;
  onClose: () => void;
  onSubmit: (goal: GoalCreate) => void;
}

const FREQUENCIES = [
  { value: "daily", label: "Daily (0DTE)" },
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
];

const STRIKE_RANGES = [
  { value: "atm5", label: "Near ATM (±5%)" },
  { value: "atm15", label: "Wide (±15%)" },
  { value: "all", label: "All available" },
];

const TIMEFRAME_OPTIONS = ["1min", "5min", "15min", "1hour", "1day"];

const PROVIDERS = ["polygon", "yfinance", "tradier", "alpaca"];

export function CreateGoalModal({ open, onClose, onSubmit }: Props) {
  const [goalType, setGoalType] = useState<"options" | "bars">("options");
  const [name, setName] = useState("");
  const [underlying, setUnderlying] = useState("QQQ");
  const [frequency, setFrequency] = useState("weekly");
  const [strikeRange, setStrikeRange] = useState("atm5");
  const [maxContracts, setMaxContracts] = useState(60);
  const [symbols, setSymbols] = useState("");
  const [timeframes, setTimeframes] = useState<string[]>(["1day"]);
  const [provider, setProvider] = useState("polygon");
  const [dateStart, setDateStart] = useState("2024-06-01");
  const [dateEnd, setDateEnd] = useState("2026-05-01");

  if (!open) return null;

  const handleSubmit = () => {
    if (!name.trim()) return;
    if (goalType === "options") {
      onSubmit({
        name,
        goal_type: "options",
        config: {
          underlying,
          provider,
          date_start: dateStart,
          date_end: dateEnd,
          frequency,
          strike_range: strikeRange,
          max_contracts_per_exp: maxContracts,
        },
      });
    } else {
      onSubmit({
        name,
        goal_type: "bars",
        config: {
          symbols: symbols.split(",").map((s) => s.trim()).filter(Boolean),
          provider,
          date_start: dateStart,
          date_end: dateEnd,
          timeframes,
        },
      });
    }
    onClose();
  };

  const toggleTimeframe = (tf: string) => {
    setTimeframes((prev) =>
      prev.includes(tf) ? prev.filter((t) => t !== tf) : [...prev, tf]
    );
  };

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-gray-900 rounded-lg w-full max-w-lg p-6 space-y-4" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-lg font-semibold text-white">Create Data Goal</h2>

        <div>
          <label className="text-xs text-gray-400 block mb-1">Goal Name</label>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g., QQQ weekly options 2Y" className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100" />
        </div>

        <div>
          <label className="text-xs text-gray-400 block mb-1">Data Type</label>
          <div className="flex gap-2">
            {(["options", "bars"] as const).map((t) => (
              <button key={t} onClick={() => setGoalType(t)}
                className={`px-4 py-1.5 rounded text-sm font-medium ${goalType === t ? "bg-indigo-600 text-white" : "bg-gray-800 text-gray-400 hover:bg-gray-700"}`}>
                {t === "options" ? "Options" : "Equities / Indexes / Crypto"}
              </button>
            ))}
          </div>
        </div>

        <div>
          <label className="text-xs text-gray-400 block mb-1">Provider</label>
          <select value={provider} onChange={(e) => setProvider(e.target.value)} className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100">
            {PROVIDERS.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs text-gray-400 block mb-1">Start Date</label>
            <input type="date" value={dateStart} onChange={(e) => setDateStart(e.target.value)} className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100" />
          </div>
          <div>
            <label className="text-xs text-gray-400 block mb-1">End Date</label>
            <input type="date" value={dateEnd} onChange={(e) => setDateEnd(e.target.value)} className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100" />
          </div>
        </div>

        {goalType === "options" && (
          <>
            <div>
              <label className="text-xs text-gray-400 block mb-1">Underlying Symbol</label>
              <input value={underlying} onChange={(e) => setUnderlying(e.target.value.toUpperCase())} className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100" />
            </div>
            <div>
              <label className="text-xs text-gray-400 block mb-1">Expiration Frequency</label>
              <div className="flex gap-2">
                {FREQUENCIES.map((f) => (
                  <button key={f.value} onClick={() => setFrequency(f.value)}
                    className={`px-3 py-1.5 rounded text-xs font-medium ${frequency === f.value ? "bg-indigo-600 text-white" : "bg-gray-800 text-gray-400 hover:bg-gray-700"}`}>
                    {f.label}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label className="text-xs text-gray-400 block mb-1">Strike Range</label>
              <div className="flex gap-2">
                {STRIKE_RANGES.map((s) => (
                  <button key={s.value} onClick={() => setStrikeRange(s.value)}
                    className={`px-3 py-1.5 rounded text-xs font-medium ${strikeRange === s.value ? "bg-indigo-600 text-white" : "bg-gray-800 text-gray-400 hover:bg-gray-700"}`}>
                    {s.label}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label className="text-xs text-gray-400 block mb-1">Max Contracts per Expiration</label>
              <input type="number" value={maxContracts} onChange={(e) => setMaxContracts(parseInt(e.target.value) || 60)} className="w-24 bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100" />
            </div>
          </>
        )}

        {goalType === "bars" && (
          <>
            <div>
              <label className="text-xs text-gray-400 block mb-1">Symbols (comma-separated)</label>
              <input value={symbols} onChange={(e) => setSymbols(e.target.value.toUpperCase())} placeholder="QQQ, SPY, AAPL" className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100" />
            </div>
            <div>
              <label className="text-xs text-gray-400 block mb-1">Timeframes</label>
              <div className="flex gap-2">
                {TIMEFRAME_OPTIONS.map((tf) => (
                  <button key={tf} onClick={() => toggleTimeframe(tf)}
                    className={`px-3 py-1.5 rounded text-xs font-medium ${timeframes.includes(tf) ? "bg-indigo-600 text-white" : "bg-gray-800 text-gray-400 hover:bg-gray-700"}`}>
                    {tf}
                  </button>
                ))}
              </div>
            </div>
          </>
        )}

        <div className="flex justify-end gap-2 pt-2">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-400 hover:text-gray-200">Cancel</button>
          <button onClick={handleSubmit} disabled={!name.trim()} className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white text-sm rounded disabled:opacity-50">Create Goal</button>
        </div>
      </div>
    </div>
  );
}
