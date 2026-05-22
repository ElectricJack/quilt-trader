// src/components/InteractiveCoverageBar.tsx
import { useRef, useState, useCallback } from "react";
import type { CoverageRange } from "../api/client";

const PROVIDER_COLORS: Record<string, string> = {
  polygon: "bg-indigo-500",
  tradier: "bg-emerald-500",
  coinbase: "bg-amber-500",
  alpaca: "bg-sky-500",
};

function providerColor(provider: string): string {
  return PROVIDER_COLORS[provider] ?? "bg-gray-400";
}

function isoToMs(iso: string): number {
  return new Date(iso).getTime();
}

function formatDateShort(ms: number): string {
  return new Date(ms).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

interface InteractiveCoverageBarProps {
  ranges: CoverageRange[];
  provider: string;
  windowStart: string;
  windowEnd: string;
  markerDate: string | null;
  onClick: (date: string) => void;
}

export function InteractiveCoverageBar({
  ranges,
  provider,
  windowStart,
  windowEnd,
  markerDate,
  onClick,
}: InteractiveCoverageBarProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [hoverX, setHoverX] = useState<number | null>(null);
  const [hoverDate, setHoverDate] = useState<string | null>(null);

  const wStartMs = isoToMs(windowStart);
  const wEndMs = isoToMs(windowEnd);
  const wSpan = wEndMs - wStartMs;

  const posToDate = useCallback(
    (clientX: number): string | null => {
      if (!containerRef.current || wSpan <= 0) return null;
      const rect = containerRef.current.getBoundingClientRect();
      const pct = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
      const ms = wStartMs + pct * wSpan;
      return new Date(ms).toISOString().slice(0, 10);
    },
    [wStartMs, wSpan],
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (!containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      setHoverX(e.clientX - rect.left);
      setHoverDate(posToDate(e.clientX));
    },
    [posToDate],
  );

  const handleMouseLeave = useCallback(() => {
    setHoverX(null);
    setHoverDate(null);
  }, []);

  const handleClick = useCallback(
    (e: React.MouseEvent) => {
      const date = posToDate(e.clientX);
      if (date) onClick(date);
    },
    [posToDate, onClick],
  );

  if (wSpan <= 0) {
    return <div className="flex-1 h-3 bg-gray-700 rounded-full" />;
  }

  const segments = ranges.map((r) => {
    const segStart = Math.max(isoToMs(r.start), wStartMs);
    const segEnd = Math.min(isoToMs(r.end) + 86_400_000, wEndMs);
    const left = ((segStart - wStartMs) / wSpan) * 100;
    const width = Math.max(((segEnd - segStart) / wSpan) * 100, 0.3);
    return { left, width };
  });

  const markerPct =
    markerDate && wSpan > 0
      ? ((isoToMs(markerDate) - wStartMs) / wSpan) * 100
      : null;

  const colorClass = providerColor(provider);

  return (
    <div
      ref={containerRef}
      className="relative flex-1 h-3 bg-gray-800 rounded cursor-crosshair group"
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      onClick={handleClick}
    >
      {segments.map((seg, i) => (
        <div
          key={i}
          className={`absolute top-0 h-full ${colorClass} rounded-sm opacity-80`}
          style={{ left: `${seg.left}%`, width: `${seg.width}%` }}
        />
      ))}

      {/* Hover crosshair */}
      {hoverX != null && (
        <>
          <div
            className="absolute top-0 h-full w-px bg-white/50 pointer-events-none"
            style={{ left: `${hoverX}px` }}
          />
          {hoverDate && (
            <div
              className="absolute -top-7 px-1.5 py-0.5 bg-gray-900 border border-gray-700 rounded text-[10px] text-gray-200 whitespace-nowrap pointer-events-none z-10"
              style={{
                left: `${hoverX}px`,
                transform: "translateX(-50%)",
              }}
            >
              {formatDateShort(isoToMs(hoverDate))}
            </div>
          )}
        </>
      )}

      {/* Persistent marker from click */}
      {markerPct != null && markerPct >= 0 && markerPct <= 100 && (
        <div
          className="absolute top-0 h-full w-px bg-indigo-300 pointer-events-none"
          style={{ left: `${markerPct}%` }}
        />
      )}
    </div>
  );
}
