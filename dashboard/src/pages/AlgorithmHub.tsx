import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useAlgorithm, useBacktestRuns, useDeployments } from "../api/hooks";
import { useResearchSessions } from "../hooks/useResearchSessions";
import { AlgorithmKpiRow } from "../components/AlgorithmKpiRow";
import { RunBacktestModal } from "../components/RunBacktestModal";
import { fmtPct, fmtNum, fmtDate } from "../lib/formatNumbers";

export function AlgorithmHub() {
  const { id = "" } = useParams<{ id: string }>();
  const { data: algo, isLoading } = useAlgorithm(id);
  const { data: runs = [] } = useBacktestRuns({ algorithm_id: id, limit: 5 });
  const { data: sessions = [] } = useResearchSessions({
    algorithm_id: id,
    status: "open,running,completed",
    limit: 5,
  });
  const { data: deployments = [] } = useDeployments({ algorithm_id: id });
  const [runBacktestOpen, setRunBacktestOpen] = useState(false);

  if (isLoading) return <p className="text-sm text-gray-400">Loading…</p>;
  if (!algo) return <p className="text-sm text-gray-400">Algorithm not found.</p>;
  const s = algo.summary;

  return (
    <div className="space-y-8">
      <div className="flex items-center gap-2">
        <button
          onClick={() => setRunBacktestOpen(true)}
          className="rounded bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-500"
        >
          Run Backtest
        </button>
      </div>

      <AlgorithmKpiRow
        sharpe={s?.headline_sharpe ?? null}
        sharpeSource={s?.headline_sharpe_source ?? null}
        openPositions={deployments.reduce((acc: number, d: any) => acc + (d.open_positions ?? 0), 0)}
      />

      <section>
        <div className="mb-2 flex items-baseline justify-between">
          <h2 className="text-lg font-medium text-gray-100">Recent backtests</h2>
          <Link to={`/algorithms/${id}/backtests`} className="text-sm text-indigo-400">
            view all {s?.counts.backtests ?? 0} →
          </Link>
        </div>
        {runs.length === 0 ? (
          <p className="text-sm text-gray-500">No backtests yet.</p>
        ) : (
          <table className="w-full text-sm">
            <tbody>
              {runs.slice(0, 5).map((r: any) => (
                <tr key={r.id} className="border-t border-gray-800">
                  <td className="px-2 py-1">
                    <Link to={`/algorithms/${id}/backtests/${r.id}`} className="text-indigo-400">
                      {r.name}
                    </Link>
                  </td>
                  <td className="px-2 py-1 text-gray-400">{r.status}</td>
                  <td className="px-2 py-1 text-gray-400">{fmtNum(r.sharpe_ratio)}</td>
                  <td className="px-2 py-1 text-gray-400">{fmtPct(r.cagr)}</td>
                  <td className="px-2 py-1 text-gray-500">{fmtDate(r.completed_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <div className="mb-2 flex items-baseline justify-between">
          <h2 className="text-lg font-medium text-gray-100">Active research</h2>
          <Link to={`/algorithms/${id}/research`} className="text-sm text-indigo-400">
            view all →
          </Link>
        </div>
        {sessions.length === 0 ? (
          <p className="text-sm text-gray-500">No active research sessions.</p>
        ) : (
          <ul className="space-y-2">
            {sessions.slice(0, 5).map((sess: any) => (
              <li key={sess.id} className="rounded border border-gray-800 bg-gray-900 p-3">
                <Link to={`/algorithms/${id}/research/${sess.id}`} className="font-medium text-indigo-400">
                  {sess.name}
                </Link>
                <span className="ml-2 text-xs text-gray-500">{sess.kind ?? "session"} · {sess.status}</span>
                {sess.progress != null && (
                  <div className="mt-1 flex items-center gap-2">
                    <div className="h-1 flex-1 overflow-hidden rounded bg-gray-800">
                      <div className="h-full bg-indigo-500" style={{ width: `${Math.round(sess.progress * 100)}%` }} />
                    </div>
                    <span className="text-xs text-gray-500">{Math.round(sess.progress * 100)}%</span>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <div className="mb-2 flex items-baseline justify-between">
          <h2 className="text-lg font-medium text-gray-100">Live deployments</h2>
          <Link to={`/algorithms/${id}/deployments`} className="text-sm text-indigo-400">
            view all →
          </Link>
        </div>
        {deployments.length === 0 ? (
          <p className="text-sm text-gray-500">
            No deployments. Deploy this algorithm to start trading.
          </p>
        ) : (
          <table className="w-full text-sm">
            <tbody>
              {deployments.slice(0, 5).map((d: any) => (
                <tr key={d.id} className="border-t border-gray-800">
                  <td className="px-2 py-1">
                    <Link to={`/algorithms/${id}/deployments/${d.id}`} className="text-indigo-400">
                      {d.account_name ?? d.account_id}
                    </Link>
                  </td>
                  <td className="px-2 py-1 text-gray-400">{d.status}</td>
                  <td className="px-2 py-1 text-gray-400">
                    {d.today_pnl != null ? `$${d.today_pnl.toFixed(2)}` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      <details className="rounded border border-gray-800 bg-gray-900">
        <summary className="cursor-pointer px-3 py-2 text-sm text-gray-300">Parameter sets</summary>
        <div className="px-3 py-2 text-sm text-gray-400">
          <Link className="text-indigo-400" to={`/algorithms/${id}/config`}>
            View parameter sets in Config →
          </Link>
        </div>
      </details>

      <details className="rounded border border-gray-800 bg-gray-900">
        <summary className="cursor-pointer px-3 py-2 text-sm text-gray-300">Manifest</summary>
        <pre className="overflow-x-auto rounded bg-gray-950 p-3 text-xs text-gray-400">
{JSON.stringify(algo.config_schema, null, 2)}
        </pre>
      </details>

      <RunBacktestModal
        open={runBacktestOpen}
        onClose={() => setRunBacktestOpen(false)}
        algorithmId={id}
        manifestConfig={
          (algo.config_schema?.parameters as Array<{ name: string; type: string; default?: unknown }>) ?? []
        }
        parameterSets={(algo as any).parameter_sets ?? []}
      />
    </div>
  );
}
