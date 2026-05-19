import { useNavigate } from "react-router-dom";
import { Widget } from "../Widget";
import { useRecentTrades } from "../../api/hooks";
import { useOverviewFilter } from "../../stores/overviewFilter";

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function fmtDollar(v: number): string {
  return `$${v.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

export function RecentTradesWidget() {
  const navigate = useNavigate();
  const { data, isLoading } = useRecentTrades(10);
  const { selectedIds } = useOverviewFilter();
  const allItems = data?.items ?? [];
  const items = allItems.filter((t) => selectedIds.has(t.account_id));

  return (
    <Widget title="Recent Trades" isLoading={isLoading} bodyClass="">
      {items.length === 0 ? (
        <p className="text-gray-500 text-sm p-3.5">No recent trades</p>
      ) : (
        items.map((t) => {
          const isBuy = (t.side ?? "").toLowerCase() === "buy";
          return (
            <div
              key={t.id}
              onClick={() => t.instance_id && navigate(`/deployments/${t.instance_id}`)}
              className="grid grid-cols-[50px_44px_80px_1fr_80px_auto] gap-2.5 items-center px-3.5 py-2 border-b border-gray-800 last:border-b-0 hover:bg-gray-800 cursor-pointer text-xs"
            >
              <span className="text-gray-500">{fmtTime(t.timestamp)}</span>
              <span className={`font-semibold ${isBuy ? "text-emerald-400" : "text-red-400"}`}>
                {isBuy ? "BUY" : "SELL"}
              </span>
              <span className="font-semibold">{t.symbol}</span>
              <span className="tabular-nums">{t.quantity} @ {t.filled_price.toLocaleString()}</span>
              <span className="text-right tabular-nums">{fmtDollar(t.notional)}</span>
              <span className="text-gray-500 truncate max-w-[110px]">{t.algorithm_name ?? "—"}</span>
            </div>
          );
        })
      )}
    </Widget>
  );
}
