import { useState } from "react";
import { Copy, Check, RefreshCw, AlertTriangle } from "lucide-react";
import { useWorkerInstallCommand, useRegenerateWorkerInstallToken } from "../api/hooks";
import { useUIStore } from "../stores/ui";

interface Props {
  workerId: string;
  workerName: string;
}

export function WorkerInstallCommand({ workerId, workerName }: Props) {
  const { data: command, isLoading, error, refetch } = useWorkerInstallCommand(workerId);
  const regenerate = useRegenerateWorkerInstallToken();
  const addAlert = useUIStore((s) => s.addAlert);
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    if (!command) return;
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      addAlert({ message: "Could not copy to clipboard.", severity: "error" });
    }
  }

  async function handleRegenerate() {
    try {
      await regenerate.mutateAsync(workerId);
      await refetch();
      addAlert({ message: "New install token issued.", severity: "success" });
    } catch (e) {
      addAlert({ message: `Couldn't regenerate token: ${(e as Error).message}`, severity: "error" });
    }
  }

  return (
    <div className="bg-gray-900 border border-amber-700/40 rounded p-4 space-y-3">
      <div className="flex items-start gap-3">
        <AlertTriangle className="text-amber-400 flex-shrink-0 mt-0.5" size={18} />
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-amber-200">Provision {workerName}</h3>
          <p className="text-xs text-gray-400 mt-1">
            SSH into the Pi and paste the one-liner below. Replace{" "}
            <code className="text-amber-300">tskey-CHANGE-ME</code> with a reusable Tailscale auth key
            (
            <a
              href="https://login.tailscale.com/admin/settings/keys"
              target="_blank"
              rel="noreferrer"
              className="text-indigo-400 hover:underline"
            >
              admin
            </a>
            ). The script installs Tailscale, joins the network, downloads the worker package, and starts a
            systemd service.
          </p>
        </div>
      </div>

      {isLoading ? (
        <p className="text-xs text-gray-500">Building command…</p>
      ) : error ? (
        <p className="text-xs text-red-400">Couldn't load install command: {(error as Error).message}</p>
      ) : (
        <div className="relative">
          <pre className="bg-black/60 border border-gray-800 rounded p-3 text-[11px] text-gray-200 font-mono whitespace-pre-wrap break-all max-h-48 overflow-y-auto">
            {command}
          </pre>
          <button
            onClick={handleCopy}
            className="absolute top-2 right-2 px-2 py-1 rounded text-xs bg-gray-800 hover:bg-gray-700 text-gray-200 flex items-center gap-1.5"
          >
            {copied ? <Check size={12} /> : <Copy size={12} />}
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
      )}

      <div className="flex items-center justify-between text-xs">
        <span className="text-gray-500">Token is single-use; regenerate to issue a fresh one.</span>
        <button
          onClick={handleRegenerate}
          disabled={regenerate.isPending}
          className="flex items-center gap-1.5 px-2 py-1 rounded text-gray-300 bg-gray-800 hover:bg-gray-700 disabled:opacity-60"
        >
          <RefreshCw size={12} className={regenerate.isPending ? "animate-spin" : ""} />
          Regenerate
        </button>
      </div>
    </div>
  );
}
