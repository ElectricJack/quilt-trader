// dashboard/src/components/strategy/DateSlider.tsx
// Range input that lets the user scrub from today to the latest leg expiry.
// The emitted value is an absolute ms-since-epoch timestamp; the parent uses
// it as the "as-of" date for the Black-Scholes pricing curve.

import { useMemo } from "react";
import type { OptionLeg } from "../../lib/options";

interface DateSliderProps {
  legs: OptionLeg[];
  valueMs: number;
  onChange: (ms: number) => void;
}

function farthestExpiryMs(legs: OptionLeg[]): number | null {
  let max: number | null = null;
  for (const l of legs) {
    const t = Date.parse(l.expiry + "T16:00:00Z");
    if (Number.isFinite(t) && (max == null || t > max)) max = t;
  }
  return max;
}

function fmtDate(ms: number): string {
  const d = new Date(ms);
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function DateSlider({ legs, valueMs, onChange }: DateSliderProps) {
  const todayMs = useMemo(() => {
    const d = new Date();
    d.setUTCHours(0, 0, 0, 0);
    return d.getTime();
  }, []);
  const maxMs = farthestExpiryMs(legs) ?? todayMs + 30 * 24 * 3600 * 1000;
  const clamped = Math.max(todayMs, Math.min(valueMs, maxMs));
  const daysToExpiry = Math.max(0, Math.round((maxMs - clamped) / (24 * 3600 * 1000)));

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900 px-3 py-2.5">
      <div className="flex items-center justify-between mb-1.5 text-xs">
        <span className="text-gray-400">As-of date</span>
        <span className="text-gray-300 tabular-nums">
          {fmtDate(clamped)}{" "}
          <span className="text-gray-500">
            ({daysToExpiry} day{daysToExpiry === 1 ? "" : "s"} to expiry)
          </span>
        </span>
      </div>
      <input
        type="range"
        min={todayMs}
        max={maxMs}
        step={24 * 3600 * 1000}
        value={clamped}
        onChange={(e) => onChange(parseInt(e.target.value, 10))}
        disabled={legs.length === 0 || maxMs <= todayMs}
        className="w-full accent-indigo-500 disabled:opacity-50"
      />
      <div className="flex justify-between text-[10px] text-gray-500 mt-0.5">
        <span>Today</span>
        <button
          onClick={() => onChange(maxMs)}
          disabled={legs.length === 0}
          className="hover:text-indigo-400 disabled:opacity-50"
        >
          Expiry ({fmtDate(maxMs)})
        </button>
      </div>
    </div>
  );
}
