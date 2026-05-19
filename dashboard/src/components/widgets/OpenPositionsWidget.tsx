import { useNavigate } from "react-router-dom";
import { Widget } from "../Widget";
import { useOpenPositions } from "../../api/hooks";
import { useOverviewFilter } from "../../stores/overviewFilter";

function formatMoney(v: number | null | undefined): string {
  if (v == null) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}$${Math.abs(v).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

function formatPct(unrealized: number | null, cost: number): string {
  if (unrealized == null || !cost) return "";
  const pct = (unrealized / Math.abs(cost)) * 100;
  const sign = pct >= 0 ? "+" : "";
  return `(${sign}${pct.toFixed(1)}%)`;
}

export function OpenPositionsWidget() {
  const navigate = useNavigate();
  const { data, isLoading } = useOpenPositions(10);
  const { selectedIds } = useOverviewFilter();
  const allItems = data?.items ?? [];
  const items = allItems.filter((p) => selectedIds.has(p.account_id));

  return (
    <Widget title="Open Positions" isLoading={isLoading} bodyClass="">
      {items.length === 0 ? (
        <p className="text-gray-500 text-sm p-3.5">No open positions</p>
      ) : (
        items.map((p) => {
          const sideLabel = (p.side ?? "").toLowerCase() === "long" ? "Long" : "Short";
          const pos = (p.unrealized_pnl ?? 0) >= 0;
          return (
            <div
              key={p.id}
              onClick={() => p.instance_id && navigate(`/deployments/${p.instance_id}`)}
              className="px-3.5 py-2.5 border-b border-gray-800 last:border-b-0 cursor-pointer hover:bg-gray-800"
            >
              <div className="flex items-center justify-between">
                <span className="text-sm">
                  <strong>{p.symbol ?? "—"}</strong>
                  {p.extra_legs > 0 && <span className="text-gray-500 ml-1">+{p.extra_legs} legs</span>}
                  <span className="text-gray-400 ml-2">· {sideLabel} {p.quantity}</span>
                </span>
                <span className={`text-xs ${pos ? "text-emerald-400" : "text-red-400"}`}>
                  {formatMoney(p.unrealized_pnl)} {formatPct(p.unrealized_pnl, p.net_cost)}
                </span>
              </div>
              <div className="flex items-center justify-between mt-1 text-[10px] text-gray-500">
                <span>
                  @ {p.avg_price ?? "—"} → {p.current_price ?? "—"}
                </span>
                <span className="text-gray-500">{p.algorithm_name ?? "—"}</span>
              </div>
            </div>
          );
        })
      )}
    </Widget>
  );
}
