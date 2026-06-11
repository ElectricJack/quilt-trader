interface Props {
  livePnl?: number;
  sharpe?: number | null;
  sharpeSource?: "live_30d" | "last_backtest" | null;
  maxDrawdown?: number | null;
  openPositions?: number;
}

function Kpi({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded border border-gray-800 bg-gray-900 px-4 py-3">
      <div className="text-xs uppercase tracking-wide text-gray-500">{label}</div>
      <div className="mt-1 text-xl font-semibold text-gray-100">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-gray-500">{sub}</div>}
    </div>
  );
}

export function AlgorithmKpiRow(props: Props) {
  const sharpeSub = props.sharpeSource === "live_30d" ? "live · 30d" :
                    props.sharpeSource === "last_backtest" ? "last backtest" : undefined;
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      <Kpi label="Live PnL" value={props.livePnl != null ? `$${props.livePnl.toFixed(2)}` : "—"} sub="today" />
      <Kpi label="Sharpe" value={props.sharpe != null ? props.sharpe.toFixed(2) : "—"} sub={sharpeSub} />
      <Kpi label="Max DD" value={props.maxDrawdown != null ? `${(props.maxDrawdown * 100).toFixed(1)}%` : "—"} sub={sharpeSub} />
      <Kpi label="Open positions" value={props.openPositions != null ? String(props.openPositions) : "—"} />
    </div>
  );
}
