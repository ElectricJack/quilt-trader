import { useEffect, type RefObject } from "react";
import type { IChartApi } from "lightweight-charts";

/**
 * Apply the container's current width to the chart and keep it in sync via
 * ResizeObserver. Call once after the chart is created.
 */
export function useChartResize(
  containerRef: RefObject<HTMLDivElement | null>,
  chart: IChartApi | null,
): void {
  useEffect(() => {
    const el = containerRef.current;
    if (!el || !chart) return;
    chart.applyOptions({ width: el.clientWidth, height: el.clientHeight });
    const obs = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) {
        chart.applyOptions({
          width: entry.contentRect.width,
          height: entry.contentRect.height,
        });
      }
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, [containerRef, chart]);
}
