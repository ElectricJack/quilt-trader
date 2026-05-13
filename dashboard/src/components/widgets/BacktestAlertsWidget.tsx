import { Widget } from "../Widget";
import { useBacktests } from "../../api/hooks";

const THRESHOLD = 95;

export function BacktestAlertsWidget() {
  const { data: backtests, isLoading } = useBacktests();

  const alerts = (backtests ?? []).filter(
    (b) => b.match_percentage < THRESHOLD
  );

  return (
    <Widget title="Backtest Alerts" isLoading={isLoading}>
      {alerts.length === 0 ? (
        <p className="text-green-400 text-sm">
          All backtests within tolerance
        </p>
      ) : (
        <ul className="space-y-2">
          {alerts.map((b) => {
            const isRed = b.match_percentage < 80;
            return (
              <li
                key={b.id}
                className="flex items-center justify-between py-1.5 border-b border-gray-800 last:border-b-0"
              >
                <span className="text-xs text-gray-400 font-mono truncate max-w-[140px]">
                  {b.instance_id}
                </span>
                <span
                  className={`text-xs font-semibold ${
                    isRed ? "text-red-400" : "text-yellow-400"
                  }`}
                >
                  {b.match_percentage.toFixed(1)}%
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </Widget>
  );
}
