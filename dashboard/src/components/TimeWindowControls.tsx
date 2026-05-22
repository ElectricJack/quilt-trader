// src/components/TimeWindowControls.tsx
import { useCallback, useMemo, useRef } from "react";

interface TimeWindowControlsProps {
  globalMin: string;
  globalMax: string;
  windowStart: string;
  windowEnd: string;
  onWindowChange: (start: string, end: string) => void;
}

function isoToMs(iso: string): number {
  return new Date(iso).getTime();
}

function msToIso(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

function generateTicks(startMs: number, endMs: number, maxTicks: number): { ms: number; label: string }[] {
  const span = endMs - startMs;
  if (span <= 0) return [];

  const DAY = 86_400_000;
  const intervals = [
    { step: 7 * DAY, fmt: { month: "short" as const, day: "numeric" as const } },
    { step: 30 * DAY, fmt: { month: "short" as const, year: "numeric" as const } },
    { step: 90 * DAY, fmt: { month: "short" as const, year: "numeric" as const } },
    { step: 365 * DAY, fmt: { year: "numeric" as const } },
  ];

  const chosen = intervals.find((i) => span / i.step <= maxTicks) ?? intervals[intervals.length - 1];
  const ticks: { ms: number; label: string }[] = [];
  let cursor = startMs + chosen.step - (startMs % chosen.step);
  while (cursor < endMs) {
    ticks.push({
      ms: cursor,
      label: new Date(cursor).toLocaleDateString(undefined, chosen.fmt),
    });
    cursor += chosen.step;
  }
  return ticks;
}

export function TimeWindowControls({
  globalMin,
  globalMax,
  windowStart,
  windowEnd,
  onWindowChange,
}: TimeWindowControlsProps) {
  const gMinMs = isoToMs(globalMin);
  const gMaxMs = isoToMs(globalMax);
  const gSpan = gMaxMs - gMinMs;

  const wStartMs = isoToMs(windowStart);
  const wEndMs = isoToMs(windowEnd);

  const trackRef = useRef<HTMLDivElement>(null);

  const ticks = useMemo(
    () => generateTicks(wStartMs, wEndMs, 8),
    [wStartMs, wEndMs],
  );

  const handleDateChange = useCallback(
    (which: "start" | "end", value: string) => {
      if (which === "start") {
        onWindowChange(value, windowEnd);
      } else {
        onWindowChange(windowStart, value);
      }
    },
    [windowStart, windowEnd, onWindowChange],
  );

  const handleSliderThumb = useCallback(
    (which: "start" | "end", e: React.MouseEvent) => {
      if (!trackRef.current || gSpan <= 0) return;
      e.preventDefault();

      const onMove = (me: MouseEvent) => {
        const rect = trackRef.current!.getBoundingClientRect();
        const pct = Math.max(0, Math.min(1, (me.clientX - rect.left) / rect.width));
        const ms = gMinMs + pct * gSpan;
        const iso = msToIso(ms);

        if (which === "start" && iso < windowEnd) {
          onWindowChange(iso, windowEnd);
        } else if (which === "end" && iso > windowStart) {
          onWindowChange(windowStart, iso);
        }
      };

      const onUp = () => {
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
      };

      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    },
    [gMinMs, gSpan, windowStart, windowEnd, onWindowChange],
  );

  const startPct = gSpan > 0 ? ((wStartMs - gMinMs) / gSpan) * 100 : 0;
  const endPct = gSpan > 0 ? ((wEndMs - gMinMs) / gSpan) * 100 : 100;

  return (
    <div className="space-y-2">
      {/* Date inputs */}
      <div className="flex items-center gap-3">
        <label className="flex items-center gap-1.5 text-xs text-gray-400">
          From
          <input
            type="date"
            value={windowStart}
            min={globalMin}
            max={windowEnd}
            onChange={(e) => handleDateChange("start", e.target.value)}
            className="bg-gray-800 border border-gray-700 text-gray-100 rounded px-2 py-1 text-xs"
          />
        </label>
        <label className="flex items-center gap-1.5 text-xs text-gray-400">
          To
          <input
            type="date"
            value={windowEnd}
            min={windowStart}
            max={globalMax}
            onChange={(e) => handleDateChange("end", e.target.value)}
            className="bg-gray-800 border border-gray-700 text-gray-100 rounded px-2 py-1 text-xs"
          />
        </label>
        <button
          onClick={() => onWindowChange(globalMin, globalMax)}
          className="text-[10px] text-gray-500 hover:text-gray-300 transition-colors"
        >
          Reset
        </button>
      </div>

      {/* Range slider */}
      <div ref={trackRef} className="relative h-4 select-none">
        {/* Track background */}
        <div className="absolute top-1.5 left-0 right-0 h-1 bg-gray-700 rounded" />
        {/* Active range */}
        <div
          className="absolute top-1.5 h-1 bg-indigo-600 rounded"
          style={{ left: `${startPct}%`, width: `${endPct - startPct}%` }}
        />
        {/* Start thumb */}
        <div
          className="absolute top-0 w-3 h-3 bg-white rounded-full cursor-ew-resize border-2 border-indigo-600 -translate-x-1/2"
          style={{ left: `${startPct}%` }}
          onMouseDown={(e) => handleSliderThumb("start", e)}
        />
        {/* End thumb */}
        <div
          className="absolute top-0 w-3 h-3 bg-white rounded-full cursor-ew-resize border-2 border-indigo-600 -translate-x-1/2"
          style={{ left: `${endPct}%` }}
          onMouseDown={(e) => handleSliderThumb("end", e)}
        />
      </div>

      {/* Time axis */}
      <div className="relative h-4">
        {ticks.map((tick, i) => {
          const pct = gSpan > 0 ? ((tick.ms - wStartMs) / (wEndMs - wStartMs)) * 100 : 0;
          if (pct < 0 || pct > 100) return null;
          return (
            <span
              key={i}
              className="absolute text-[9px] text-gray-600 -translate-x-1/2 whitespace-nowrap"
              style={{ left: `${pct}%` }}
            >
              {tick.label}
            </span>
          );
        })}
      </div>
    </div>
  );
}
