import { useNavigate } from "react-router-dom";
import { Widget } from "../Widget";
import { useAccountSnapshotsLatest } from "../../api/hooks";
import { useOverviewFilter } from "../../stores/overviewFilter";

function fmtMoney(v: number): string {
  return `$${v.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

export function AccountBalancesWidget() {
  const navigate = useNavigate();
  const { data, isLoading } = useAccountSnapshotsLatest();
  const { selectedIds } = useOverviewFilter();
  const allItems = data?.items ?? [];
  const items = allItems.filter((a) => selectedIds.has(a.account_id));

  return (
    <Widget title="Account Balances" isLoading={isLoading} bodyClass="">
      {items.length === 0 ? (
        <p className="text-gray-500 text-sm p-3.5">No accounts configured</p>
      ) : (
        items.map((a) => {
          const total = a.latest.total_value;
          const positionsPct = total > 0 ? (a.latest.positions_value / total) * 100 : 0;
          const cashPct = total > 0 ? (a.latest.cash / total) * 100 : 0;
          const dayPct = a.day_pct;
          return (
            <div
              key={a.account_id}
              onClick={() => navigate(`/accounts/${a.account_id}`)}
              className="px-3.5 py-3 border-b border-gray-800 last:border-b-0 cursor-pointer hover:bg-gray-800"
            >
              <div className="flex items-baseline justify-between mb-1.5">
                <strong className="text-xs">{a.account_name}</strong>
                <span className="text-sm font-semibold">
                  {fmtMoney(total)}{" "}
                  {dayPct !== null && (
                    <span className={`text-[11px] ${dayPct >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                      {dayPct >= 0 ? "+" : ""}{dayPct.toFixed(1)}%
                    </span>
                  )}
                </span>
              </div>
              <div className="flex h-3 rounded-sm overflow-hidden mb-1">
                <div style={{ width: `${positionsPct}%`, background: "#3b82f6" }} />
                <div style={{ width: `${cashPct}%`, background: "#10b981" }} />
              </div>
              <div className="text-[10px] text-gray-500">
                {positionsPct.toFixed(0)}% positions ({fmtMoney(a.latest.positions_value)})
                {" · "}
                {cashPct.toFixed(0)}% cash ({fmtMoney(a.latest.cash)})
              </div>
            </div>
          );
        })
      )}
    </Widget>
  );
}
