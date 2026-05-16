import type { IChartApi } from "lightweight-charts";

/**
 * Attach a ResizeObserver that keeps the chart's width and height in sync
 * with the container. Call inside the chart-creation effect (where the
 * chart instance is in scope) and include the returned disposer in the
 * effect's cleanup. A standalone hook approach doesn't work because the
 * chart is held in a ref — mutating a ref doesn't trigger a re-render, so
 * a hook that takes `chart` as an argument never sees the actual chart
 * instance on the same pass it's created in.
 */
export function attachChartResize(
  el: HTMLElement, chart: IChartApi,
): () => void {
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
}
