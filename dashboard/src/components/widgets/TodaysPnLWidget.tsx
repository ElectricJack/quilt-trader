import { Widget } from "../Widget";
import { useAllInstances } from "../../api/hooks";

function formatDollar(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
  }).format(value);
}

function extractPnl(metrics: Record<string, unknown> | null): number | null {
  if (!metrics) return null;
  const val = metrics["total_pnl"];
  if (typeof val === "number") return val;
  return null;
}

export function TodaysPnLWidget() {
  const { data: instances, isLoading } = useAllInstances();

  const instancesWithPnl = (instances ?? [])
    .map((inst) => ({
      id: inst.id,
      pnl: extractPnl(inst.lifetime_metrics),
    }))
    .filter((x): x is { id: string; pnl: number } => x.pnl !== null);

  const total = instancesWithPnl.reduce((sum, x) => sum + x.pnl, 0);
  const isPositive = total >= 0;

  return (
    <Widget title="Lifetime P&L" isLoading={isLoading}>
      <div
        className={`text-4xl font-bold mb-4 ${
          isPositive ? "text-green-400" : "text-red-400"
        }`}
      >
        {instancesWithPnl.length === 0 ? "—" : formatDollar(total)}
      </div>

      {instancesWithPnl.length === 0 ? (
        <p className="text-gray-500 text-sm">No P&L data available</p>
      ) : (
        <ul className="space-y-1">
          {instancesWithPnl.map(({ id, pnl }) => (
            <li
              key={id}
              className="flex items-center justify-between py-1 border-b border-gray-800 last:border-b-0"
            >
              <span className="text-xs text-gray-400 font-mono truncate max-w-[130px]">
                {id}
              </span>
              <span
                className={`text-xs font-medium ${
                  pnl >= 0 ? "text-green-400" : "text-red-400"
                }`}
              >
                {formatDollar(pnl)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Widget>
  );
}
