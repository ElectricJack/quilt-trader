import { useNavigate } from "react-router-dom";
import { Widget } from "../Widget";
import { Sparkline } from "../Sparkline";
import { useDeployments } from "../../api/hooks";

function formatMoney(v: number): string {
  const sign = v >= 0 ? "+" : "";
  return `${sign}${new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD", maximumFractionDigits: 0,
  }).format(v)}`;
}

export function AlgorithmsWidget() {
  const navigate = useNavigate();
  const { data: instances, isLoading } = useDeployments();

  const rows = (instances ?? []).map((inst) => {
    const metrics = inst.lifetime_metrics ?? {};
    const lifetime = typeof metrics.total_pnl === "number" ? metrics.total_pnl : 0;
    const tradeCount = typeof metrics.trade_count === "number" ? metrics.trade_count : 0;
    const winRate = typeof metrics.win_rate === "number" ? metrics.win_rate : 0;
    return {
      id: inst.id,
      name: inst.algorithm_name ?? inst.id.slice(0, 8),
      account: inst.account_name ?? "—",
      status: inst.status,
      today: 0,
      sparkline: [],
      trades: tradeCount,
      win_rate: winRate,
      lifetime,
    };
  });
  rows.sort((a, b) => b.lifetime - a.lifetime);

  const total = rows.reduce((s, r) => s + r.lifetime, 0);
  const runningRows = rows.filter((r) => r.status === "running");
  const running = runningRows.length;
  const stopped = rows.length - running;

  return (
    <Widget title="Running Algorithms" isLoading={isLoading} bodyClass="">
      <div className="px-3.5 py-3 border-b border-gray-800">
        <div className={`text-xl font-bold ${total >= 0 ? "text-emerald-400" : "text-red-400"}`}>
          {formatMoney(total)}
          <span className="text-xs font-normal text-gray-400 ml-2">
            lifetime · {running} running · {stopped} stopped
          </span>
        </div>
      </div>
      <div className="grid grid-cols-[1fr_80px_44px_44px_76px] gap-2 px-3.5 py-1.5 bg-gray-950 border-b border-gray-800 text-[9px] uppercase tracking-wide text-gray-500">
        <span>Algorithm</span><span>P&L Curve</span>
        <span className="text-right">Trades</span><span className="text-right">Win %</span><span className="text-right">Lifetime</span>
      </div>
      {runningRows.length === 0 && (
        <div className="px-3.5 py-4 text-xs text-gray-500 text-center">
          No running deployments.
        </div>
      )}
      {runningRows.map((r) => (
        <div
          key={r.id}
          onClick={() => navigate(`/deployments/${r.id}`)}
          className="grid grid-cols-[1fr_80px_44px_44px_76px] gap-2 px-3.5 py-2.5 border-b border-gray-800 last:border-b-0 hover:bg-gray-800 cursor-pointer items-center"
        >
          <div>
            <div className="text-xs font-semibold text-gray-200 leading-tight">
              <span
                className={`inline-block w-1.5 h-1.5 rounded-full mr-1.5 align-middle ${
                  r.status === "running" ? "bg-emerald-500" : "bg-gray-500"
                }`}
              />
              {r.name}
            </div>
            <div className="text-[9px] text-gray-500 mt-0.5">
              {r.account} · today{" "}
              <span className={r.today >= 0 ? "text-emerald-400" : "text-red-400"}>
                {formatMoney(r.today)}
              </span>
            </div>
          </div>
          <Sparkline
            points={r.sparkline}
            color={r.status === "running" ? "#10b981" : "#6b7280"}
          />
          <span className="text-right text-xs tabular-nums">{r.trades}</span>
          <span className={`text-right text-xs tabular-nums ${r.win_rate >= 50 ? "text-emerald-400" : ""}`}>
            {(r.win_rate * 100).toFixed(0)}%
          </span>
          <span className={`text-right text-xs tabular-nums font-semibold ${r.lifetime >= 0 ? "text-emerald-400" : "text-red-400"}`}>
            {formatMoney(r.lifetime)}
          </span>
        </div>
      ))}
    </Widget>
  );
}
