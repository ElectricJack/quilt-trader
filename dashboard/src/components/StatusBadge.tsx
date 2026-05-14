import clsx from "clsx";

type Status =
  | "running"
  | "stopped"
  | "error"
  | "installed"
  | "installing"
  | "online"
  | "offline"
  | "idle"
  | string;

const STATUS_CLASSES: Record<string, string> = {
  running: "bg-green-900 text-green-300",
  online: "bg-green-900 text-green-300",
  installed: "bg-blue-900 text-blue-300",
  installing: "bg-yellow-900 text-yellow-300",
  idle: "bg-gray-700 text-gray-300",
  stopped: "bg-gray-700 text-gray-300",
  offline: "bg-gray-700 text-gray-300",
  error: "bg-red-900 text-red-300",
};

interface StatusBadgeProps {
  status: Status;
  className?: string;
}

export function StatusBadge({ status, className }: StatusBadgeProps) {
  const colorClass =
    STATUS_CLASSES[status.toLowerCase()] ?? "bg-gray-700 text-gray-300";

  return (
    <span
      className={clsx(
        "inline-flex items-center px-2 py-0.5 rounded text-xs font-medium capitalize",
        colorClass,
        className
      )}
    >
      {status}
    </span>
  );
}
