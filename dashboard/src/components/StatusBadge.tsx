import clsx from "clsx";

const STATUSES: Record<string, { label: string; classes: string }> = {
  // Deployment vocabulary
  stopped:  { label: "Stopped",  classes: "bg-gray-800 text-gray-300 border-gray-700" },
  starting: { label: "Starting", classes: "bg-yellow-900/40 text-yellow-300 border-yellow-800" },
  running:  { label: "Running",  classes: "bg-green-900/40 text-green-300 border-green-800" },
  stopping: { label: "Stopping", classes: "bg-yellow-900/40 text-yellow-300 border-yellow-800" },
  error:    { label: "Error",    classes: "bg-red-900/40 text-red-300 border-red-800" },
  // Worker statuses
  offline:  { label: "Offline",  classes: "bg-gray-800 text-gray-400 border-gray-700" },
  online:   { label: "Online",   classes: "bg-green-900/40 text-green-300 border-green-800" },
  // Backtest run statuses (kept for the BacktestRunDetail page)
  queued:           { label: "Queued",      classes: "bg-gray-800 text-gray-300 border-gray-700" },
  downloading_data: { label: "Downloading", classes: "bg-blue-900/40 text-blue-300 border-blue-800" },
  completed:        { label: "Completed",   classes: "bg-green-900/40 text-green-300 border-green-800" },
  failed:           { label: "Failed",      classes: "bg-red-900/40 text-red-300 border-red-800" },
  cancelled:        { label: "Cancelled",   classes: "bg-gray-800 text-gray-400 border-gray-700" },
  // Install statuses
  pending:   { label: "Pending",   classes: "bg-amber-900/40 text-amber-300 border-amber-800" },
  installed: { label: "Installed", classes: "bg-green-900/40 text-green-300 border-green-800" },
  claimed:   { label: "Claimed",   classes: "bg-green-900/40 text-green-300 border-green-800" },
  idle:      { label: "Idle",      classes: "bg-gray-800 text-gray-300 border-gray-700" },
};

interface StatusBadgeProps {
  status: string;
  className?: string;
}

export function StatusBadge({ status, className }: StatusBadgeProps) {
  const cfg = STATUSES[status.toLowerCase()];
  if (!cfg) {
    return (
      <span
        className={clsx(
          "inline-flex items-center px-2 py-0.5 rounded text-xs border bg-gray-800 text-gray-400 border-gray-700",
          className,
        )}
      >
        {status}
      </span>
    );
  }
  return (
    <span
      className={clsx(
        "inline-flex items-center px-2 py-0.5 rounded text-xs border",
        cfg.classes,
        className,
      )}
    >
      {cfg.label}
    </span>
  );
}
