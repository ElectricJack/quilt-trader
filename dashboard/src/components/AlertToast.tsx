import { useEffect, useRef } from "react";
import { X } from "lucide-react";
import { useUIStore, type Alert } from "../stores/ui";

const severityClasses: Record<Alert["severity"], string> = {
  info: "bg-blue-900 border-blue-700 text-blue-200",
  warning: "bg-yellow-900 border-yellow-700 text-yellow-200",
  error: "bg-red-900 border-red-700 text-red-200",
  success: "bg-green-900 border-green-700 text-green-200",
};

const AUTO_DISMISS_MS = 5000;

function Toast({ alert }: { alert: Alert }) {
  const dismissAlert = useUIStore((s) => s.dismissAlert);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    timerRef.current = setTimeout(() => {
      dismissAlert(alert.id);
    }, AUTO_DISMISS_MS);

    return () => {
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
      }
    };
  }, [alert.id, dismissAlert]);

  return (
    <div
      className={`border rounded-lg px-4 py-3 shadow-lg text-sm max-w-sm flex items-start gap-2 ${severityClasses[alert.severity]}`}
      role="alert"
    >
      <span className="flex-1 break-words">{alert.message}</span>
      <button
        onClick={() => dismissAlert(alert.id)}
        className="shrink-0 mt-0.5 opacity-70 hover:opacity-100 transition-opacity"
        aria-label="Dismiss notification"
      >
        <X size={14} />
      </button>
    </div>
  );
}

export function AlertToast() {
  const alerts = useUIStore((s) => s.alerts);

  if (alerts.length === 0) return null;

  return (
    <div className="fixed top-4 right-4 z-50 flex flex-col gap-2">
      {alerts.map((alert) => (
        <Toast key={alert.id} alert={alert} />
      ))}
    </div>
  );
}
