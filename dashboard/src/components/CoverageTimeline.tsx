/**
 * CoverageTimeline — horizontal bar showing green segments for covered date ranges.
 *
 * Props:
 *   ranges   — array of {start, end} ISO date strings (e.g. "2026-01-02")
 *   barStart — overall window start (ISO date string); defaults to earliest range start
 *   barEnd   — overall window end (ISO date string); defaults to today
 */

interface DateRange {
  start: string;
  end: string;
}

interface CoverageTimelineProps {
  ranges: DateRange[];
  barStart?: string;
  barEnd?: string;
}

function isoToMs(iso: string): number {
  return new Date(iso).getTime();
}

function formatDateLabel(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export function CoverageTimeline({ ranges, barStart, barEnd }: CoverageTimelineProps) {
  const today = new Date().toISOString().slice(0, 10);

  // Determine the overall bar window.
  const windowStart = barStart
    ? isoToMs(barStart)
    : ranges.length > 0
    ? isoToMs(ranges[0].start)
    : isoToMs(today);

  const windowEnd = barEnd
    ? isoToMs(barEnd)
    : isoToMs(today);

  const windowSpan = windowEnd - windowStart;

  if (windowSpan <= 0 || ranges.length === 0) {
    return (
      <div className="flex items-center gap-2 w-full">
        <div className="flex-1 h-2 bg-gray-700 rounded-full" />
        <span className="text-xs text-gray-500 whitespace-nowrap">no data</span>
      </div>
    );
  }

  // Build segments.
  const segments = ranges.map((r) => {
    const segStart = Math.max(isoToMs(r.start), windowStart);
    const segEnd = Math.min(isoToMs(r.end) + 86_400_000 /* include end day */, windowEnd);
    const left = ((segStart - windowStart) / windowSpan) * 100;
    const width = Math.max(((segEnd - segStart) / windowSpan) * 100, 0.5);
    return { left, width };
  });

  // Date range label: earliest start → latest end.
  const labelStart = formatDateLabel(ranges[0].start);
  const labelEnd = formatDateLabel(ranges[ranges.length - 1].end);
  const labelText = labelStart === labelEnd ? labelStart : `${labelStart} – ${labelEnd}`;

  return (
    <div className="flex items-center gap-2 w-full min-w-0">
      {/* Bar container */}
      <div className="relative flex-1 h-2 bg-gray-700 rounded-full overflow-hidden">
        {segments.map((seg, i) => (
          <div
            key={i}
            className="absolute top-0 h-full bg-emerald-500 rounded-sm"
            style={{ left: `${seg.left}%`, width: `${seg.width}%` }}
          />
        ))}
      </div>
      {/* Date range label */}
      <span className="text-xs text-gray-400 whitespace-nowrap shrink-0">{labelText}</span>
    </div>
  );
}
