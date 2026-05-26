import { Widget } from "../Widget";
import { usePortfolioKpis, useWebSocketTopic } from "../../api/hooks";

function formatMoney(v: number, compact = false): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    notation: compact ? "compact" : "standard",
    maximumFractionDigits: compact ? 1 : 0,
  }).format(v);
}

function pct(v: number): string {
  return `${v.toFixed(1)}%`;
}

export function KpiStripWidget() {
  const { data, isLoading } = usePortfolioKpis();
  const livePortfolio = useWebSocketTopic<{ total_equity: number }>("portfolio:summary");

  const totalEquity = livePortfolio?.total_equity ?? data?.total_equity;

  const kpis: { label: string; value: string; sub: string; pos?: boolean; neg?: boolean }[] = [
    {
      label: "Today P&L",
      value: data ? formatMoney(data.today_pnl) : "—",
      sub: data ? pct(data.today_pnl_pct) : "",
      pos: (data?.today_pnl ?? 0) > 0,
      neg: (data?.today_pnl ?? 0) < 0,
    },
    {
      label: "Total Equity",
      value: totalEquity != null ? formatMoney(totalEquity, true) : "—",
      sub: "all accounts",
    },
    {
      label: "Trades Today",
      value: data ? String(data.trades_today) : "—",
      sub: data ? `${data.trades_today_wins} win · ${data.trades_today_losses} loss` : "",
    },
    {
      label: "Win Rate",
      value: data ? pct(data.win_rate) : "—",
      sub: data ? `7d avg ${pct(data.win_rate_7d_avg)}` : "",
      pos: (data?.win_rate ?? 0) >= 50,
    },
    {
      label: "Open Positions",
      value: data ? String(data.open_positions) : "—",
      sub: data ? `${data.open_positions_long} long · ${data.open_positions_short} short` : "",
    },
    {
      label: "Daily VaR (95%)",
      value: data ? formatMoney(data.open_risk, true) : "—",
      sub: data ? `${pct(data.open_risk_pct_equity)} of equity` : "",
    },
    {
      label: "Deployed",
      value: data ? pct(data.deployed_pct) : "—",
      sub: data ? formatMoney(data.deployed_usd, true) : "",
    },
    {
      label: "Buying Power",
      value: data ? formatMoney(data.buying_power, true) : "—",
      sub: data ? `${pct(data.buying_power_pct)} available` : "",
    },
  ];

  return (
    <Widget title="Today's KPIs" isLoading={isLoading} bodyClass="">
      <div className="grid grid-cols-4 grid-rows-2 gap-px bg-gray-800 h-full min-h-[180px]">
        {kpis.map((k) => (
          <div key={k.label} className="bg-gray-950 px-4 py-3 flex flex-col justify-center">
            <div className="text-[10px] uppercase tracking-wide text-gray-400 font-medium">{k.label}</div>
            <div
              className={`text-2xl font-bold mt-1 leading-tight ${
                k.pos ? "text-emerald-400" : k.neg ? "text-red-400" : "text-white"
              }`}
            >
              {k.value}
            </div>
            {k.sub && <div className="text-[10px] text-gray-500 mt-0.5">{k.sub}</div>}
          </div>
        ))}
      </div>
    </Widget>
  );
}
