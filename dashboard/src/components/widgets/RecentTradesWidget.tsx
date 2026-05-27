import { useNavigate } from "react-router-dom";
import { Widget } from "../Widget";
import { useRecentTrades } from "../../api/hooks";
import { useOverviewFilter } from "../../stores/overviewFilter";

// Format a trade timestamp in the user's local timezone.
// Relative date (Today / Yesterday) up to 7 days, then "Mon May 22".
// Always shows local time + a TZ abbreviation so the timezone is unambiguous.
function fmtTimestamp(iso: string | null): { date: string; time: string } {
  if (!iso) return { date: "—", time: "" };
  const d = new Date(iso);
  if (isNaN(d.getTime())) return { date: "—", time: "" };

  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startOfTradeDay = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const dayDiff = Math.round(
    (startOfToday.getTime() - startOfTradeDay.getTime()) / 86_400_000
  );

  let date: string;
  if (dayDiff === 0) date = "Today";
  else if (dayDiff === 1) date = "Yesterday";
  else if (dayDiff > 1 && dayDiff < 7) {
    date = d.toLocaleDateString([], { weekday: "short" });
  } else {
    date = d.toLocaleDateString([], { month: "short", day: "numeric" });
  }

  // Local time + zone abbreviation. e.g. "9:34 AM EDT"
  const time = d.toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });
  return { date, time };
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
          const { date, time } = fmtTimestamp(t.timestamp);
          return (
            <div
              key={t.id}
              onClick={() => t.instance_id && navigate(`/deployments/${t.instance_id}`)}
              className="grid grid-cols-[140px_44px_80px_1fr_80px_auto] gap-2.5 items-center px-3.5 py-2 border-b border-gray-800 last:border-b-0 hover:bg-gray-800 cursor-pointer text-xs"
            >
              <span className="text-gray-500 tabular-nums">
                <span className="text-gray-400">{date}</span>{" "}
                <span>{time}</span>
              </span>
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
