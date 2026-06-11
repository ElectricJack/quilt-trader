import type { CPCVResult } from "../../types";

function Kpi({ label, value, sub, badgeColor }: {
  label: string; value: string; sub?: string; badgeColor?: "emerald" | "red";
}) {
  const badge =
    badgeColor === "emerald" ? "bg-emerald-500/15 text-emerald-400"
    : badgeColor === "red" ? "bg-red-500/15 text-red-400"
    : "bg-gray-800 text-gray-100";
  const dataTestId = label.toLowerCase().includes("deflated") ? "dsr-badge" : undefined;
  return (
    <div className="rounded border border-gray-800 bg-gray-900 px-4 py-3">
      <div className="text-xs uppercase tracking-wide text-gray-500">{label}</div>
      <div data-testid={dataTestId} className={`mt-1 inline-block rounded px-2 py-0.5 text-xl font-semibold ${badge}`}>
        {value}
      </div>
      {sub && <div className="mt-0.5 text-xs text-gray-500">{sub}</div>}
    </div>
  );
}

export function CpcvSummaryCard({ result }: { result: CPCVResult }) {
  const s = result.summary;
  if (result.mode === "fixed") {
    return (
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Kpi label="Mean Segment Sharpe" value={s.mean_segment_sharpe?.toFixed(2) ?? "—"} />
        <Kpi label="Median" value={s.median_segment_sharpe?.toFixed(2) ?? "—"} />
        <Kpi label="Std" value={s.std_segment_sharpe?.toFixed(2) ?? "—"} />
        <Kpi label="95% CI" value={`${s.bootstrap_ci_lower.toFixed(2)} … ${s.bootstrap_ci_upper.toFixed(2)}`} />
      </div>
    );
  }
  const dsrColor: "emerald" | "red" = (s.deflated_sharpe_ratio ?? 0) > 0 ? "emerald" : "red";
  const psrColor: "emerald" | "red" = (s.probabilistic_sharpe_zero ?? 0) > 0.95 ? "emerald" : "red";
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
      <Kpi label="Mean Path Sharpe" value={s.mean_path_sharpe?.toFixed(2) ?? "—"} />
      <Kpi label="Median" value={s.median_path_sharpe?.toFixed(2) ?? "—"} />
      <Kpi label="Std" value={s.std_path_sharpe?.toFixed(2) ?? "—"} />
      <Kpi label="95% CI" value={`${s.bootstrap_ci_lower.toFixed(2)} … ${s.bootstrap_ci_upper.toFixed(2)}`} />
      <Kpi label="Deflated Sharpe" value={s.deflated_sharpe_ratio?.toFixed(2) ?? "—"} badgeColor={dsrColor} />
      <Kpi label="PSR(0)" value={s.probabilistic_sharpe_zero?.toFixed(2) ?? "—"} badgeColor={psrColor} />
    </div>
  );
}
