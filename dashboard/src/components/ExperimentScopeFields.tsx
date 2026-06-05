import { useEffect } from "react";

interface Props {
  startDate: string;            // ISO YYYY-MM-DD or ""
  endDate: string;
  initialCash: number;
  costProfile: string;
  benchmarkSymbol: string;      // "" when unset
  benchmarkSource: string;      // "" when unset
  mtmRealism: number;
  onChange: (next: {
    date_range_start: string;
    date_range_end: string;
    initial_cash: number;
    cost_profile: string;
    benchmark_symbol: string | null;
    benchmark_source: string | null;
    mtm_realism: number;
  }) => void;
  onValidityChange?: (valid: boolean) => void;
  disabled?: boolean;
}

function isValid(p: Props): boolean {
  if (!p.startDate || !p.endDate) return false;
  if (p.endDate <= p.startDate) return false;
  if (!(p.initialCash > 0)) return false;
  if (!p.costProfile.trim()) return false;
  const bsEmpty = !p.benchmarkSymbol.trim();
  const bSrcEmpty = !p.benchmarkSource.trim();
  if (bsEmpty !== bSrcEmpty) return false;     // pair violation
  if (p.mtmRealism < 0 || p.mtmRealism > 1) return false;
  return true;
}

export function ExperimentScopeFields(props: Props) {
  const {
    startDate, endDate, initialCash, costProfile,
    benchmarkSymbol, benchmarkSource, mtmRealism,
    onChange, onValidityChange, disabled,
  } = props;

  // Notify parent of validity whenever inputs change.
  useEffect(() => {
    onValidityChange?.(isValid(props));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [startDate, endDate, initialCash, costProfile, benchmarkSymbol, benchmarkSource, mtmRealism]);

  function emit(overrides: Partial<Props>) {
    const merged = { ...props, ...overrides };
    onChange({
      date_range_start: merged.startDate,
      date_range_end: merged.endDate,
      initial_cash: merged.initialCash,
      cost_profile: merged.costProfile,
      benchmark_symbol: merged.benchmarkSymbol.trim() || null,
      benchmark_source: merged.benchmarkSource.trim() || null,
      mtm_realism: merged.mtmRealism,
    });
  }

  const input =
    "bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 w-full";

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-4 gap-2">
        <div className="space-y-1">
          <label htmlFor="sf-start" className="text-sm text-gray-300">
            Start date <span className="text-red-400">*</span>
          </label>
          <input
            id="sf-start" type="date" value={startDate} disabled={disabled}
            onChange={(e) => emit({ startDate: e.target.value })}
            className={input}
          />
        </div>
        <div className="space-y-1">
          <label htmlFor="sf-end" className="text-sm text-gray-300">
            End date <span className="text-red-400">*</span>
          </label>
          <input
            id="sf-end" type="date" value={endDate} disabled={disabled}
            onChange={(e) => emit({ endDate: e.target.value })}
            className={input}
          />
        </div>
        <div className="space-y-1">
          <label htmlFor="sf-cash" className="text-sm text-gray-300">
            Initial cash <span className="text-red-400">*</span>
          </label>
          <input
            id="sf-cash" type="number" min={1} value={initialCash} disabled={disabled}
            onChange={(e) => emit({ initialCash: parseFloat(e.target.value || "0") })}
            className={input}
          />
        </div>
        <div className="space-y-1">
          <label htmlFor="sf-cost" className="text-sm text-gray-300">
            Cost profile <span className="text-red-400">*</span>
          </label>
          <input
            id="sf-cost" type="text" value={costProfile} disabled={disabled}
            onChange={(e) => emit({ costProfile: e.target.value })}
            className={input}
          />
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div className="space-y-1">
          <label htmlFor="sf-bsym" className="text-sm text-gray-300">
            Benchmark symbol (optional, paired)
          </label>
          <input
            id="sf-bsym" type="text" value={benchmarkSymbol} disabled={disabled}
            placeholder="e.g. SPY"
            onChange={(e) => emit({ benchmarkSymbol: e.target.value })}
            className={input}
          />
        </div>
        <div className="space-y-1">
          <label htmlFor="sf-bsrc" className="text-sm text-gray-300">
            Benchmark source (optional, paired)
          </label>
          <input
            id="sf-bsrc" type="text" value={benchmarkSource} disabled={disabled}
            placeholder="e.g. polygon"
            onChange={(e) => emit({ benchmarkSource: e.target.value })}
            className={input}
          />
        </div>
      </div>
      <div className="grid grid-cols-1 gap-2">
        <div className="space-y-1">
          <label htmlFor="sf-mtm" className="text-sm text-gray-300">
            MTM realism (0 = conservative, 1 = broker-like)
          </label>
          <input
            id="sf-mtm" type="number" min={0} max={1} step={0.05}
            value={mtmRealism} disabled={disabled}
            onChange={(e) => emit({ mtmRealism: parseFloat(e.target.value || "0") })}
            className={input}
          />
        </div>
      </div>
    </div>
  );
}
