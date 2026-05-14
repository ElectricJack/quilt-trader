import clsx from "clsx";

interface MetricsCardProps {
  label: string;
  value: string | number;
  change?: number;
  className?: string;
}

export function MetricsCard({ label, value, change, className }: MetricsCardProps) {
  const isProfit = change !== undefined && change >= 0;
  const isLoss = change !== undefined && change < 0;

  return (
    <div
      className={clsx(
        "bg-gray-900 border border-gray-800 rounded-lg p-4 flex flex-col gap-1",
        className
      )}
    >
      <span className="text-xs text-gray-400 uppercase tracking-wide">{label}</span>
      <span className="text-2xl font-bold text-white">{value}</span>
      {change !== undefined && (
        <span
          className={clsx(
            "text-sm font-medium",
            isProfit && "text-profit",
            isLoss && "text-loss"
          )}
        >
          {isProfit ? "+" : ""}
          {change.toFixed(2)}%
        </span>
      )}
    </div>
  );
}
