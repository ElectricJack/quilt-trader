import { useNavigate } from "react-router-dom";
import { Widget } from "../Widget";
import { useAlerts } from "../../api/hooks";

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function AlertsWidget() {
  const navigate = useNavigate();
  const { data, isLoading } = useAlerts(10);
  const items = data?.items ?? [];

  return (
    <Widget title="Alerts" isLoading={isLoading} bodyClass="">
      {items.length === 0 ? (
        <p className="text-gray-500 text-sm p-3.5">No alerts</p>
      ) : (
        items.map((a) => {
          const pillBg =
            a.pill_color === "err"
              ? "bg-red-950 text-red-300"
              : a.pill_color === "backtest"
                ? "bg-blue-950 text-blue-300"
                : "bg-yellow-950 text-yellow-400";
          return (
            <div
              key={`${a.kind}-${a.id}`}
              onClick={() => a.link_path && navigate(a.link_path)}
              className="flex gap-2.5 items-center px-3.5 py-2.5 border-b border-gray-800 last:border-b-0 cursor-pointer hover:bg-gray-800"
            >
              <span
                className={`px-2 py-0.5 rounded text-[10px] font-semibold min-w-[42px] text-center ${pillBg}`}
              >
                {a.pill}
              </span>
              <div className="text-xs">
                <strong>{a.label}</strong>
                <div className="text-[10px] text-gray-500">
                  {a.source_name} · {fmtTime(a.timestamp)}
                </div>
              </div>
            </div>
          );
        })
      )}
    </Widget>
  );
}
