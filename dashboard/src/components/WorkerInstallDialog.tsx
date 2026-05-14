import { useCallback, useEffect, useState } from "react";
import { Copy, Check, AlertTriangle, RefreshCw } from "lucide-react";
import {
  useCreateWorker,
  useWorkerInstallCommand,
  useRegenerateWorkerInstallToken,
  useWorker,
} from "../api/hooks";
import { useWorkerConnectedEvent } from "../hooks/useWorkerConnectedEvent";

type Step = "form" | "waiting" | "connected";

interface Props {
  open: boolean;
  onClose: () => void;
}

export function WorkerInstallDialog({ open, onClose }: Props) {
  const [step, setStep] = useState<Step>("form");
  const [name, setName] = useState("");
  const [maxAlgos, setMaxAlgos] = useState(2);
  const [workerId, setWorkerId] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const createWorker = useCreateWorker();
  const { data: command, refetch: refetchCommand } = useWorkerInstallCommand(
    workerId ?? "",
    !!workerId
  );
  const regen = useRegenerateWorkerInstallToken();

  // Fallback polling
  const { data: worker, refetch: refetchWorker } = useWorker(workerId ?? "");
  useEffect(() => {
    if (step !== "waiting" || !workerId) return;
    const id = setInterval(() => {
      void refetchWorker();
    }, 5000);
    return () => clearInterval(id);
  }, [step, workerId, refetchWorker]);

  useEffect(() => {
    if (step === "waiting" && worker?.status === "online") {
      setStep("connected");
    }
  }, [step, worker?.status]);

  // Stable callback to avoid re-subscribing on every render
  const handleConnected = useCallback(() => setStep("connected"), []);
  useWorkerConnectedEvent(step === "waiting" ? workerId : null, handleConnected);

  // Auto-close after connected
  useEffect(() => {
    if (step !== "connected") return;
    const id = setTimeout(() => {
      onClose();
      reset();
    }, 1500);
    return () => clearTimeout(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step]);

  function reset() {
    setStep("form");
    setName("");
    setMaxAlgos(2);
    setWorkerId(null);
    setCopied(false);
  }

  async function handleRegister(e: React.FormEvent) {
    e.preventDefault();
    const created = await createWorker.mutateAsync({
      name,
      max_algorithms: maxAlgos,
      tailscale_ip: null,
    } as any); // eslint-disable-line @typescript-eslint/no-explicit-any
    setWorkerId(created.id);
    setStep("waiting");
  }

  async function handleCopy() {
    if (!command) return;
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // ignore clipboard errors
    }
  }

  if (!open) return null;

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
      <div className="bg-gray-900 border border-gray-700 rounded-lg p-5 w-full max-w-2xl">
        {step === "form" && (
          <form onSubmit={(e) => void handleRegister(e)}>
            <h2 className="text-lg font-semibold text-gray-100 mb-3">Add Worker</h2>
            <label className="block mb-2 text-sm text-gray-300">
              Name
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                className="block w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm mt-1 text-gray-100"
                placeholder="e.g. worker-1"
              />
            </label>
            <label className="block mb-4 text-sm text-gray-300">
              Max algorithms
              <input
                type="number"
                value={maxAlgos}
                onChange={(e) => setMaxAlgos(Number(e.target.value))}
                min={1}
                className="block w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm mt-1 text-gray-100"
              />
            </label>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={onClose}
                className="px-3 py-1.5 rounded text-sm text-gray-300 bg-gray-700 hover:bg-gray-600"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={!name || createWorker.isPending}
                className="px-3 py-1.5 rounded text-sm text-white bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50"
              >
                {createWorker.isPending ? "Generating…" : "Generate Install Command"}
              </button>
            </div>
          </form>
        )}

        {step === "waiting" && (
          <div>
            <div className="flex items-center gap-2 mb-3">
              <AlertTriangle className="text-amber-400 flex-shrink-0" size={18} />
              <h2 className="text-lg font-semibold text-gray-100">Waiting for {name}</h2>
            </div>
            <p className="text-xs text-gray-400 mb-2">
              SSH into the Pi and paste this. Replace{" "}
              <code className="text-amber-300">tskey-CHANGE-ME</code> with a real Tailscale auth key.
            </p>
            <div className="relative mb-3">
              <pre className="bg-black/60 border border-gray-800 rounded p-3 text-[11px] text-gray-200 font-mono whitespace-pre-wrap break-all max-h-48 overflow-y-auto">
                {command ?? "Building command…"}
              </pre>
              <button
                onClick={() => void handleCopy()}
                className="absolute top-2 right-2 px-2 py-1 rounded text-xs bg-gray-800 hover:bg-gray-700 text-gray-200 flex items-center gap-1.5"
              >
                {copied ? <Check size={12} /> : <Copy size={12} />}
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
            <p className="text-sm text-gray-400 mb-3">Waiting for worker to connect…</p>
            <div className="flex items-center justify-between">
              <button
                onClick={async () => {
                  await regen.mutateAsync(workerId!);
                  void refetchCommand();
                }}
                disabled={regen.isPending}
                className="flex items-center gap-1.5 text-xs text-gray-400 hover:text-gray-200 disabled:opacity-50"
              >
                <RefreshCw size={12} className={regen.isPending ? "animate-spin" : ""} />
                Regenerate token
              </button>
              <button
                onClick={() => {
                  onClose();
                  reset();
                }}
                className="px-3 py-1.5 rounded text-sm text-gray-300 bg-gray-700 hover:bg-gray-600"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {step === "connected" && (
          <div className="text-center py-6">
            <Check className="mx-auto text-green-400 mb-2" size={32} />
            <p className="text-lg font-semibold text-green-200">Connected!</p>
            <p className="text-xs text-gray-400 mt-1">{name} is online.</p>
          </div>
        )}
      </div>
    </div>
  );
}
