import { useMemo, useState } from "react";
import { z } from "zod";
import { useAlgorithms, useInstallAlgorithmFromUrl } from "../api/hooks";
import { AlgorithmCard } from "../components/AlgorithmCard";
import { FormModal } from "../components/FormModal";
import { FormField } from "../components/FormField";
import { useUIStore } from "../stores/ui";

type Sort = "activity" | "name" | "sharpe" | "backtests";

const installSchema = z.object({
  repo_url: z.string().url("Must be a valid URL").refine(
    (v) => /^https?:\/\/github\.com\/[^/]+\/[^/]+/.test(v),
    "Must be a GitHub repo URL",
  ),
});
type InstallForm = z.infer<typeof installSchema>;

function sortFor(algos: any[], sort: Sort) {
  const copy = [...algos];
  switch (sort) {
    case "name":
      copy.sort((a, b) => a.name.localeCompare(b.name));
      break;
    case "sharpe":
      copy.sort((a, b) => (b.summary?.headline_sharpe ?? -Infinity) - (a.summary?.headline_sharpe ?? -Infinity));
      break;
    case "backtests":
      copy.sort((a, b) => (b.summary?.counts.backtests ?? 0) - (a.summary?.counts.backtests ?? 0));
      break;
    case "activity":
    default:
      copy.sort((a, b) => {
        const da = a.summary?.last_activity_at ? new Date(a.summary.last_activity_at).getTime() : 0;
        const db = b.summary?.last_activity_at ? new Date(b.summary.last_activity_at).getTime() : 0;
        return db - da;
      });
  }
  return copy;
}

export function AlgorithmsGrid() {
  const { data, isLoading } = useAlgorithms();
  const { mutateAsync: installFromUrl, isPending: isInstalling } = useInstallAlgorithmFromUrl();
  const addAlert = useUIStore((s) => s.addAlert);

  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<Sort>("activity");
  const [installOpen, setInstallOpen] = useState(false);

  const view = useMemo(() => {
    if (!data) return [];
    const filtered = search
      ? data.filter((a: any) => a.name.toLowerCase().includes(search.toLowerCase()))
      : data;
    return sortFor(filtered, sort);
  }, [data, search, sort]);

  const running = useMemo(
    () => view.filter((a: any) => {
      const st = a.summary?.status;
      return st === "live" || st === "paper";
    }),
    [view],
  );
  const others = useMemo(
    () => view.filter((a: any) => {
      const st = a.summary?.status;
      return st !== "live" && st !== "paper";
    }),
    [view],
  );

  async function handleInstall(formData: InstallForm) {
    try {
      await installFromUrl(formData.repo_url);
      addAlert({ message: `Installing ${formData.repo_url}…`, severity: "info" });
      setInstallOpen(false);
    } catch {
      addAlert({ message: "Failed to start installation.", severity: "error" });
    }
  }

  if (isLoading) return <p className="p-6 text-gray-400">Loading algorithms…</p>;

  return (
    <div className="px-6 py-4">
      {/* Header */}
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">
          Algorithms{" "}
          {data && (
            <span className="text-base font-normal text-gray-400">
              ({data.length})
            </span>
          )}
        </h1>
        <button
          onClick={() => setInstallOpen(true)}
          className="rounded px-4 py-2 text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500 transition-colors"
        >
          Install from GitHub
        </button>
      </div>

      {data && data.length === 0 ? (
        <div className="px-6 py-12 text-center">
          <p className="text-lg text-gray-300">No algorithms installed yet.</p>
          <p className="mt-2 text-sm text-gray-500">
            Install your first algorithm to start backtesting and deploying.
          </p>
        </div>
      ) : (
        <>
          {/* Search + sort row */}
          <div className="mb-4 flex items-center gap-3">
            <input
              placeholder="Search algorithms…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-64 rounded border border-gray-800 bg-gray-950 px-3 py-1.5 text-sm text-gray-100"
            />
            <label className="flex items-center gap-2 text-xs text-gray-400">
              Sort
              <select
                value={sort}
                onChange={(e) => setSort(e.target.value as Sort)}
                className="rounded border border-gray-800 bg-gray-950 px-2 py-1 text-xs text-gray-100"
              >
                <option value="activity">Last activity</option>
                <option value="name">Name</option>
                <option value="sharpe">Sharpe</option>
                <option value="backtests"># backtests</option>
              </select>
            </label>
          </div>

          {view.length === 0 ? (
            <p className="text-sm text-gray-500">No algorithms match your search.</p>
          ) : (
            <div className="space-y-5">
              {running.length > 0 && (
                <section>
                  <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">
                    Running <span className="text-gray-500">({running.length})</span>
                  </h2>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    {running.map((a: any) => (
                      <AlgorithmCard key={a.id} algorithm={a} />
                    ))}
                  </div>
                </section>
              )}
              {others.length > 0 && (
                <section>
                  <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-400">
                    All algorithms <span className="text-gray-500">({others.length})</span>
                  </h2>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    {others.map((a: any) => (
                      <AlgorithmCard key={a.id} algorithm={a} />
                    ))}
                  </div>
                </section>
              )}
            </div>
          )}
        </>
      )}

      {/* Install from GitHub modal */}
      <FormModal
        open={installOpen}
        onClose={() => setInstallOpen(false)}
        title="Install from GitHub"
        schema={installSchema}
        defaultValues={{ repo_url: "" }}
        onSubmit={handleInstall}
        submitLabel="Install"
        isSubmitting={isInstalling}
      >
        {(form) => (
          <FormField label="Repository URL" error={form.formState.errors.repo_url?.message}>
            <input
              {...form.register("repo_url")}
              className="bg-gray-800 border border-gray-700 text-gray-100 rounded px-3 py-2 text-sm w-full"
              placeholder="https://github.com/owner/algorithm-repo"
            />
            <p className="text-xs text-gray-500 mt-1">
              Public repos work without a PAT. Private repos require GitHub PAT in Settings.
            </p>
          </FormField>
        )}
      </FormModal>
    </div>
  );
}
