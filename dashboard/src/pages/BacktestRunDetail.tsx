// ── Spec D U2: backtest run detail ──
import { useParams, Link } from "react-router-dom";
import { ChevronLeft, Download, Trash2 } from "lucide-react";
import {
  useBacktestRun,
  useBacktestEquityCurve,
  useBacktestTrades,
  useDeleteBacktestRun,
} from "../api/hooks";
import { StatusBadge } from "../components/StatusBadge";
import { EquityCurve } from "../components/EquityCurve";

function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(2)}%`;
}

function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null) return "—";
  return v.toLocaleString("en-US", { maximumFractionDigits: digits });
}

function fmtUsd(v: number | null | undefined): string {
  if (v == null) return "—";
  return v.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

const INFLIGHT_STATUSES = ["queued", "downloading_data", "running"];
function inflight(status: string | undefined | null): boolean {
  return !!status && INFLIGHT_STATUSES.includes(status);
}

interface BacktestTradeRow {
  timestamp: string;
  symbol: string;
  side: string;
  quantity: number;
  requested_price: number | null;
  fill_price: number | null;
  slippage_dollars: number | null;
  fees: number | null;
  realized_pnl: number | null;
}

export function BacktestRunDetail() {
  const { id = "" } = useParams<{ id: string }>();
  const { data: run } = useBacktestRun(id, { refetchInterval: 2000 });
  const { data: equity } = useBacktestEquityCurve(id);
  const { data: tradesData } = useBacktestTrades(id, 500);
  const del = useDeleteBacktestRun();

  if (!run) {
    return <div className="p-4 text-gray-400">Loading…</div>;
  }

  const isRunInflight = inflight(run.status);
  const trades = ((tradesData?.items ?? []) as BacktestTradeRow[]) ?? [];
  const totalTrades =
    (tradesData as { total?: number } | undefined)?.total ?? trades.length;
  const equityPoints = (equity?.items ?? []).map((p) => ({
    timestamp: p.timestamp,
    equity: p.portfolio_value,
  }));

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link to="/backtests" className="text-gray-400 hover:text-white">
            <ChevronLeft size={20} />
          </Link>
          <h1 className="text-xl font-bold">Backtest Run</h1>
          <StatusBadge status={run.status} />
        </div>
        <div className="flex gap-2">
          {run.tearsheet_path && (
            <a
              href={`/api/backtest-runs/${id}/tearsheet`}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-1 px-3 py-1.5 rounded text-sm text-gray-300 bg-gray-700 hover:bg-gray-600"
            >
              <Download size={14} /> Download tearsheet
            </a>
          )}
          <button
            onClick={() => del.mutate(id)}
            disabled={del.isPending}
            className="flex items-center gap-1 px-3 py-1.5 rounded text-sm text-red-300 bg-red-900/40 border border-red-800 hover:bg-red-900/60 disabled:opacity-50"
          >
            <Trash2 size={14} /> Delete
          </button>
        </div>
      </div>

      {/* In-flight progress */}
      {isRunInflight && (
        <div className="bg-gray-900 border border-gray-800 rounded p-3">
          <div className="text-sm text-gray-300 mb-2">
            {run.progress_message ?? run.status}
          </div>
          <div className="bg-gray-700 rounded-full h-2 overflow-hidden">
            <div
              className="bg-indigo-600 h-2 transition-all duration-300"
              style={{ width: `${(run.progress_pct ?? 0) * 100}%` }}
            />
          </div>
        </div>
      )}

      {/* Error block */}
      {run.error_message && (
        <div className="bg-red-900/30 border border-red-800 rounded p-3 text-sm text-red-200 whitespace-pre-wrap">
          {run.error_message}
        </div>
      )}

      {/* Metrics grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {(
          [
            ["Total return", fmtPct(run.total_return), run.total_return],
            ["CAGR", fmtPct(run.cagr), run.cagr],
            ["Sharpe", fmtNum(run.sharpe_ratio), run.sharpe_ratio],
            ["Sortino", fmtNum(run.sortino_ratio), run.sortino_ratio],
            ["Calmar", fmtNum(run.calmar_ratio), run.calmar_ratio],
            ["Max drawdown", fmtPct(run.max_drawdown), -1],
            ["Volatility", fmtPct(run.volatility), null],
            ["RoMaD", fmtNum(run.romad), run.romad],
            ["Trade count", fmtNum(run.trade_count, 0), null],
            ["Win rate", fmtPct(run.win_rate), null],
            ["Profit factor", fmtNum(run.profit_factor), null],
            ["Expectancy", fmtUsd(run.expectancy), run.expectancy],
            ["Total fees", fmtUsd(run.total_fees_paid), -1],
            ["Total slippage", fmtUsd(run.total_slippage_dollars), -1],
            [
              "Longest win streak",
              fmtNum(run.longest_winning_streak, 0),
              null,
            ],
            [
              "Longest loss streak",
              fmtNum(run.longest_losing_streak, 0),
              null,
            ],
          ] as Array<[string, string, number | null]>
        ).map(([label, value, signed]) => (
          <div
            key={label}
            className="bg-gray-900 border border-gray-800 rounded p-3"
          >
            <div className="text-[10px] uppercase tracking-wide text-gray-500">
              {label}
            </div>
            <div
              className={`text-lg font-semibold ${
                typeof signed === "number" && signed > 0
                  ? "text-green-400"
                  : typeof signed === "number" && signed < 0
                    ? "text-red-400"
                    : "text-gray-200"
              }`}
            >
              {value}
            </div>
          </div>
        ))}
      </div>

      {/* Equity curve */}
      <div className="bg-gray-900 border border-gray-800 rounded p-3">
        <h3 className="text-sm font-semibold text-gray-300 mb-2">
          Equity curve
        </h3>
        {equityPoints.length > 0 ? (
          <EquityCurve data={equityPoints} height={300} />
        ) : (
          <div className="text-gray-500 text-sm py-8 text-center">
            No equity data yet
          </div>
        )}
      </div>

      {/* Trades */}
      <div className="bg-gray-900 border border-gray-800 rounded">
        <div className="px-3 py-2 border-b border-gray-800 text-sm font-semibold text-gray-300">
          Trades ({totalTrades})
        </div>
        <div className="overflow-auto max-h-96">
          <table className="w-full text-sm">
            <thead className="bg-gray-800 text-xs uppercase text-gray-400 sticky top-0">
              <tr>
                <th className="text-left p-2">Timestamp</th>
                <th className="text-left p-2">Symbol</th>
                <th className="text-left p-2">Side</th>
                <th className="text-right p-2">Qty</th>
                <th className="text-right p-2">Requested</th>
                <th className="text-right p-2">Fill</th>
                <th className="text-right p-2">Slippage $</th>
                <th className="text-right p-2">Fees</th>
                <th className="text-right p-2">Realized P&amp;L</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t, i) => (
                <tr key={i} className="border-t border-gray-800">
                  <td className="p-2 text-xs text-gray-400">
                    {new Date(t.timestamp).toLocaleString()}
                  </td>
                  <td className="p-2 font-mono">{t.symbol}</td>
                  <td
                    className={`p-2 ${
                      t.side === "buy" ? "text-green-400" : "text-red-400"
                    }`}
                  >
                    {t.side}
                  </td>
                  <td className="p-2 text-right">{fmtNum(t.quantity, 4)}</td>
                  <td className="p-2 text-right">
                    {fmtUsd(t.requested_price)}
                  </td>
                  <td className="p-2 text-right font-semibold">
                    {fmtUsd(t.fill_price)}
                  </td>
                  <td className="p-2 text-right text-gray-400">
                    {fmtUsd(t.slippage_dollars)}
                  </td>
                  <td className="p-2 text-right text-gray-400">
                    {fmtUsd(t.fees)}
                  </td>
                  <td
                    className={`p-2 text-right ${
                      t.realized_pnl == null
                        ? "text-gray-500"
                        : t.realized_pnl > 0
                          ? "text-green-400"
                          : "text-red-400"
                    }`}
                  >
                    {t.realized_pnl == null ? "—" : fmtUsd(t.realized_pnl)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
