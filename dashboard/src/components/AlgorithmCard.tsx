import { Link } from "react-router-dom";
import type { Algorithm } from "../types";
import { AlgorithmStatusBadge } from "./AlgorithmStatusBadge";
import { EquitySparkline } from "./EquitySparkline";

function fmt(n: number) {
  return n.toFixed(2);
}

function ago(iso: string | null): string {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function AlgorithmCard({ algorithm }: { algorithm: Algorithm }) {
  const s = algorithm.summary;
  return (
    <Link
      to={`/algorithms/${algorithm.id}`}
      className="block rounded-lg border border-gray-800 bg-gray-900 p-4 hover:border-gray-700"
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="font-medium text-gray-100">{algorithm.name}</span>
        {s && <AlgorithmStatusBadge status={s.status} />}
      </div>
      <div className="mt-0.5 flex items-baseline justify-between text-xs text-gray-500">
        <span>v{algorithm.version ?? "—"}</span>
        {s?.headline_sharpe != null && (
          <span>
            Sharpe {fmt(s.headline_sharpe)}
            <span className="ml-1 text-gray-600">· {s.headline_sharpe_source === "live_30d" ? "30d" : "backtest"}</span>
          </span>
        )}
      </div>
      <div className="mt-3">
        <EquitySparkline points={s?.equity_sparkline ?? []} width={240} height={36} />
      </div>
      {s && (
        <div className="mt-3 text-xs text-gray-400">
          {s.counts.deployments} deployments · {s.counts.backtests} backtests · {s.counts.research_sessions} research
        </div>
      )}
      <div className="mt-1 text-xs text-gray-600">Last run {ago(s?.last_activity_at ?? null)}</div>
    </Link>
  );
}
