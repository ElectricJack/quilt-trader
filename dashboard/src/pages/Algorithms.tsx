import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Trash2, Search, RefreshCw } from "lucide-react";
import { z } from "zod";
import {
  useAlgorithms,
  useDeleteAlgorithm,
  useInstallAlgorithm,
  useGithubRepos,
  useUpdateAlgorithm,
} from "../api/hooks";
import { FormModal } from "../components/FormModal";
import { FormField } from "../components/FormField";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { StatusBadge } from "../components/StatusBadge";
import { GitStatusBadge } from "../components/GitStatusBadge";
import { useUIStore } from "../stores/ui";

const installSchema = z.object({
  full_name: z.string().min(1),
});
type InstallForm = z.infer<typeof installSchema>;

export function Algorithms() {
  const navigate = useNavigate();
  const { data: algorithms, isLoading } = useAlgorithms();
  const { mutateAsync: deleteAlgorithm } = useDeleteAlgorithm();
  const { mutateAsync: installAlgorithm, isPending: isInstalling } =
    useInstallAlgorithm();
  const { mutateAsync: updateAlgorithm } = useUpdateAlgorithm();
  const addAlert = useUIStore((s) => s.addAlert);

  const [search, setSearch] = useState("");
  const [installOpen, setInstallOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<{
    id: string;
    name: string;
  } | null>(null);
  const [updateTarget, setUpdateTarget] = useState<{
    id: string;
    name: string;
  } | null>(null);
  const [updatingId, setUpdatingId] = useState<string | null>(null);

  // Only fetch repos when the modal is open
  const { data: repos, isLoading: reposLoading } = useGithubRepos(installOpen);

  const filtered = (algorithms ?? []).filter((a) =>
    a.name.toLowerCase().includes(search.toLowerCase())
  );

  async function handleInstall(data: InstallForm) {
    try {
      await installAlgorithm(data.full_name);
      addAlert({ message: `Installing ${data.full_name}…`, severity: "info" });
      setInstallOpen(false);
    } catch {
      addAlert({ message: "Failed to start installation.", severity: "error" });
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return;
    try {
      await deleteAlgorithm(deleteTarget.id);
      addAlert({
        message: `Deleted algorithm "${deleteTarget.name}".`,
        severity: "success",
      });
    } catch {
      addAlert({ message: "Failed to delete algorithm.", severity: "error" });
    } finally {
      setDeleteTarget(null);
    }
  }

  async function handleUpdate() {
    if (!updateTarget) return;
    const target = updateTarget;
    setUpdateTarget(null);
    setUpdatingId(target.id);
    try {
      await updateAlgorithm(target.id);
      addAlert({
        message: `Updated "${target.name}" to latest commit.`,
        severity: "success",
      });
    } catch {
      addAlert({
        message: `Failed to update "${target.name}".`,
        severity: "error",
      });
    } finally {
      setUpdatingId(null);
    }
  }

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">
          Algorithms{" "}
          {!isLoading && (
            <span className="text-gray-400 text-base font-normal">
              ({algorithms?.length ?? 0})
            </span>
          )}
        </h1>
        <button
          onClick={() => setInstallOpen(true)}
          className="px-4 py-2 rounded text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500 transition-colors"
        >
          Install from GitHub
        </button>
      </div>

      {/* Search */}
      <div className="relative max-w-sm">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500 pointer-events-none" />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search algorithms..."
          className="bg-gray-800 border border-gray-700 text-gray-100 rounded px-3 py-2 text-sm w-full max-w-sm pl-9"
        />
      </div>

      {/* Content */}
      {isLoading ? (
        <p className="text-gray-400 text-sm">Loading…</p>
      ) : filtered.length === 0 ? (
        <p className="text-gray-500 text-sm">
          {(algorithms ?? []).length === 0
            ? "No algorithms installed. Install your first algorithm from GitHub."
            : "No algorithms match your search."}
        </p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {filtered.map((a) => (
            <div
              key={a.id}
              className="bg-gray-900 border border-gray-800 rounded-lg p-4 cursor-pointer hover:border-gray-700 transition-colors"
              onClick={() => navigate(`/algorithms/${a.id}`)}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  {/* Name + status badge */}
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-sm font-medium text-gray-100">
                      {a.name}
                    </span>
                    <StatusBadge status={a.install_status} />
                  </div>

                  {/* Description */}
                  {a.description && (
                    <p className="text-xs text-gray-400 mt-1 line-clamp-2">
                      {a.description}
                    </p>
                  )}

                  {/* Version + commit + git-status badge + update button */}
                  <div className="flex items-center gap-2 flex-wrap mt-2">
                    {a.version && (
                      <span className="text-xs text-gray-500">v{a.version}</span>
                    )}
                    <span
                      className="text-xs font-mono text-gray-500"
                      title={a.commit_hash ?? undefined}
                    >
                      {a.commit_hash ? a.commit_hash.slice(0, 7) : "—"}
                    </span>
                    <GitStatusBadge algoId={a.id} />
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setUpdateTarget({ id: a.id, name: a.name });
                      }}
                      disabled={updatingId === a.id}
                      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] text-gray-300 bg-gray-800 hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                      aria-label="Update algorithm"
                      title="Pull latest from GitHub"
                    >
                      <RefreshCw
                        className={`w-3 h-3 ${updatingId === a.id ? "animate-spin" : ""}`}
                      />
                      {updatingId === a.id ? "Updating…" : "Update"}
                    </button>
                  </div>

                  {/* Repo URL */}
                  <p className="text-xs text-gray-600 mt-1 truncate">
                    {a.repo_url}
                  </p>
                </div>

                {/* Delete button */}
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setDeleteTarget({ id: a.id, name: a.name });
                  }}
                  className="flex-shrink-0 p-1.5 rounded text-gray-500 hover:text-red-400 hover:bg-gray-800 transition-colors"
                  aria-label="Delete algorithm"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Install from GitHub modal */}
      <FormModal
        open={installOpen}
        onClose={() => setInstallOpen(false)}
        title="Install from GitHub"
        schema={installSchema}
        defaultValues={{ full_name: "" }}
        onSubmit={handleInstall}
        submitLabel="Install"
        isSubmitting={isInstalling}
      >
        {(form) => (
          <FormField
            label="Repository"
            error={form.formState.errors.full_name?.message}
          >
            <select
              {...form.register("full_name")}
              className="bg-gray-800 border border-gray-700 text-gray-100 rounded px-3 py-2 text-sm w-full"
              disabled={reposLoading}
            >
              <option value="">
                {reposLoading ? "Loading repositories…" : "Select a repository"}
              </option>
              {(repos ?? []).map((repo) => (
                <option key={repo.full_name} value={repo.full_name}>
                  {repo.full_name}
                </option>
              ))}
            </select>
            {/* Description of selected repo */}
            {(() => {
              const selected = form.watch("full_name");
              const repo = (repos ?? []).find((r) => r.full_name === selected);
              return repo?.description ? (
                <p className="text-xs text-gray-500 mt-1">{repo.description}</p>
              ) : null;
            })()}
          </FormField>
        )}
      </FormModal>

      {/* Delete confirm dialog */}
      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete Algorithm"
        message={`Are you sure you want to delete "${deleteTarget?.name}"? This cannot be undone.`}
        confirmLabel="Delete"
        onConfirm={handleDelete}
        onCancel={() => setDeleteTarget(null)}
      />

      {/* Update confirm dialog */}
      <ConfirmDialog
        open={updateTarget !== null}
        title="Update Algorithm"
        message={`Pull the latest commit for "${updateTarget?.name}" from GitHub?`}
        confirmLabel="Update"
        onConfirm={handleUpdate}
        onCancel={() => setUpdateTarget(null)}
      />
    </div>
  );
}
