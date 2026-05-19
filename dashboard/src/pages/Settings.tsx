import { useState } from "react";
import {
  useSettings,
  useSetGithubPat,
  useSetDiscordToken,
  useSetPolygonKey,
  useSetThetaData,
  useSetCoordinatorIp,
  useSetTailscaleAuthkey,
  useDeleteGithubPat,
  useDeleteDiscordToken,
  useDeletePolygonKey,
  useDeleteThetaData,
  useDeleteCoordinatorIp,
  useDeleteTailscaleAuthkey,
} from "../api/hooks";
import { FormField } from "../components/FormField";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useUIStore } from "../stores/ui";

// ─── Shared Styles ────────────────────────────────────────────────────────────

const INPUT_CLS =
  "bg-gray-800 border border-gray-700 text-gray-100 rounded px-3 py-2 text-sm w-full focus:outline-none focus:ring-1 focus:ring-indigo-500";
const SAVE_BTN_CLS =
  "bg-indigo-600 hover:bg-indigo-500 text-white text-sm px-4 py-2 rounded disabled:opacity-50 disabled:cursor-not-allowed";
const CLEAR_BTN_CLS =
  "bg-red-600/20 hover:bg-red-600/30 text-red-400 text-sm px-4 py-2 rounded disabled:opacity-50 disabled:cursor-not-allowed";

// ─── Status Badge ─────────────────────────────────────────────────────────────

function StatusBadge({ isSet }: { isSet: boolean }) {
  return (
    <span
      className={`text-xs font-medium px-2 py-0.5 rounded ${
        isSet ? "bg-green-900 text-green-300" : "bg-gray-700 text-gray-400"
      }`}
    >
      {isSet ? "Configured" : "Not set"}
    </span>
  );
}

// ─── Single-value Credential Card ─────────────────────────────────────────────

interface SingleCredentialCardProps {
  label: string;
  isSet: boolean;
  inputLabel: string;
  helpLink?: { href: string; label: string };
  onSave: (value: string) => Promise<void>;
  onDelete: () => Promise<void>;
  isSaving: boolean;
  isDeleting: boolean;
}

function SingleCredentialCard({
  label,
  isSet,
  inputLabel,
  helpLink,
  onSave,
  onDelete,
  isSaving,
  isDeleting,
}: SingleCredentialCardProps) {
  const [value, setValue] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);

  const handleSave = async () => {
    if (!value.trim()) return;
    await onSave(value.trim());
    setValue("");
  };

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-3">
          <span className="text-sm font-medium text-gray-200">{label}</span>
          {helpLink && (
            <a
              href={helpLink.href}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-indigo-400 hover:underline"
            >
              {helpLink.label} ↗
            </a>
          )}
        </div>
        <StatusBadge isSet={isSet} />
      </div>

      {/* Body */}
      {isSet ? (
        <>
          <button
            className={CLEAR_BTN_CLS}
            onClick={() => setConfirmOpen(true)}
            disabled={isDeleting}
          >
            {isDeleting ? "Clearing…" : "Clear"}
          </button>
          <ConfirmDialog
            open={confirmOpen}
            title={`Clear ${label}`}
            message={`Are you sure you want to remove the ${label}? Any services relying on it will stop working.`}
            confirmLabel="Clear"
            onConfirm={async () => {
              setConfirmOpen(false);
              await onDelete();
            }}
            onCancel={() => setConfirmOpen(false)}
          />
        </>
      ) : (
        <div className="flex items-end gap-3">
          <FormField label={inputLabel} className="flex-1">
            <input
              type="password"
              className={INPUT_CLS}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void handleSave();
              }}
              placeholder="Paste value…"
              autoComplete="off"
            />
          </FormField>
          <button
            className={SAVE_BTN_CLS}
            onClick={() => void handleSave()}
            disabled={isSaving || !value.trim()}
          >
            {isSaving ? "Saving…" : "Save"}
          </button>
        </div>
      )}
    </div>
  );
}

// ─── Visible Value Card (non-secret, shows current value) ────────────────────

interface VisibleValueCardProps {
  label: string;
  value: string | null;
  inputLabel: string;
  placeholder?: string;
  help?: string;
  onSave: (value: string) => Promise<void>;
  onDelete: () => Promise<void>;
  isSaving: boolean;
  isDeleting: boolean;
}

function VisibleValueCard({
  label,
  value,
  inputLabel,
  placeholder,
  help,
  onSave,
  onDelete,
  isSaving,
  isDeleting,
}: VisibleValueCardProps) {
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(false);

  const handleSave = async () => {
    if (!draft.trim()) return;
    await onSave(draft.trim());
    setDraft("");
    setEditing(false);
  };

  const isSet = !!value;
  const showEditor = editing || !isSet;

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm font-medium text-gray-200">{label}</span>
        <StatusBadge isSet={isSet} />
      </div>

      {help && <p className="text-xs text-gray-500 mb-3">{help}</p>}

      {isSet && !showEditor && (
        <div className="flex items-center gap-3">
          <code className="flex-1 bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-100 font-mono break-all">
            {value}
          </code>
          <button
            className={SAVE_BTN_CLS}
            onClick={() => {
              setDraft(value ?? "");
              setEditing(true);
            }}
          >
            Edit
          </button>
          <button
            className={CLEAR_BTN_CLS}
            onClick={() => void onDelete()}
            disabled={isDeleting}
          >
            {isDeleting ? "Clearing…" : "Clear"}
          </button>
        </div>
      )}

      {showEditor && (
        <div className="flex items-end gap-3">
          <FormField label={inputLabel} className="flex-1">
            <input
              type="text"
              className={INPUT_CLS}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void handleSave();
                if (e.key === "Escape") {
                  setEditing(false);
                  setDraft("");
                }
              }}
              placeholder={placeholder}
              autoComplete="off"
            />
          </FormField>
          <button
            className={SAVE_BTN_CLS}
            onClick={() => void handleSave()}
            disabled={isSaving || !draft.trim()}
          >
            {isSaving ? "Saving…" : "Save"}
          </button>
          {editing && (
            <button
              className="text-sm text-gray-400 hover:text-gray-200 px-2"
              onClick={() => {
                setEditing(false);
                setDraft("");
              }}
            >
              Cancel
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ─── ThetaData Card ───────────────────────────────────────────────────────────

interface ThetaDataCardProps {
  isSet: boolean;
  onSave: (username: string, password: string) => Promise<void>;
  onDelete: () => Promise<void>;
  isSaving: boolean;
  isDeleting: boolean;
}

function ThetaDataCard({
  isSet,
  onSave,
  onDelete,
  isSaving,
  isDeleting,
}: ThetaDataCardProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);

  const handleSave = async () => {
    if (!username.trim() || !password.trim()) return;
    await onSave(username.trim(), password.trim());
    setUsername("");
    setPassword("");
  };

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm font-medium text-gray-200">
          ThetaData Credentials
        </span>
        <StatusBadge isSet={isSet} />
      </div>

      {/* Body */}
      {isSet ? (
        <>
          <button
            className={CLEAR_BTN_CLS}
            onClick={() => setConfirmOpen(true)}
            disabled={isDeleting}
          >
            {isDeleting ? "Clearing…" : "Clear"}
          </button>
          <ConfirmDialog
            open={confirmOpen}
            title="Clear ThetaData Credentials"
            message="Are you sure you want to remove the ThetaData credentials? Any services relying on them will stop working."
            confirmLabel="Clear"
            onConfirm={async () => {
              setConfirmOpen(false);
              await onDelete();
            }}
            onCancel={() => setConfirmOpen(false)}
          />
        </>
      ) : (
        <div className="space-y-3">
          <div className="flex items-end gap-3">
            <FormField label="Username" className="flex-1">
              <input
                type="text"
                className={INPUT_CLS}
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="ThetaData username"
                autoComplete="off"
              />
            </FormField>
            <FormField label="Password" className="flex-1">
              <input
                type="password"
                className={INPUT_CLS}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void handleSave();
                }}
                placeholder="ThetaData password"
                autoComplete="off"
              />
            </FormField>
            <button
              className={SAVE_BTN_CLS}
              onClick={() => void handleSave()}
              disabled={isSaving || !username.trim() || !password.trim()}
            >
              {isSaving ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Settings Page ────────────────────────────────────────────────────────────

export function Settings() {
  const { data: settings, isLoading } = useSettings();
  const addAlert = useUIStore((s) => s.addAlert);

  const setGithubPat = useSetGithubPat();
  const setDiscordToken = useSetDiscordToken();
  const setPolygonKey = useSetPolygonKey();
  const setThetaData = useSetThetaData();
  const setCoordinatorIp = useSetCoordinatorIp();
  const setTailscaleAuthkey = useSetTailscaleAuthkey();

  const deleteGithubPat = useDeleteGithubPat();
  const deleteDiscordToken = useDeleteDiscordToken();
  const deletePolygonKey = useDeletePolygonKey();
  const deleteThetaData = useDeleteThetaData();
  const deleteCoordinatorIp = useDeleteCoordinatorIp();
  const deleteTailscaleAuthkey = useDeleteTailscaleAuthkey();

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-white">Settings</h1>

      {isLoading ? (
        <p className="text-gray-400 text-sm">Loading…</p>
      ) : (
        <div className="space-y-3">
          <SingleCredentialCard
            label="GitHub PAT"
            isSet={settings?.github_pat_set ?? false}
            inputLabel="Personal Access Token"
            onSave={async (value) => {
              await setGithubPat.mutateAsync(value);
              addAlert({ message: "GitHub PAT saved.", severity: "success" });
            }}
            onDelete={async () => {
              await deleteGithubPat.mutateAsync();
              addAlert({ message: "GitHub PAT cleared.", severity: "success" });
            }}
            isSaving={setGithubPat.isPending}
            isDeleting={deleteGithubPat.isPending}
          />

          <SingleCredentialCard
            label="Discord Bot Token"
            isSet={settings?.discord_bot_token_set ?? false}
            inputLabel="Bot Token"
            onSave={async (value) => {
              await setDiscordToken.mutateAsync(value);
              addAlert({
                message: "Discord Bot Token saved.",
                severity: "success",
              });
            }}
            onDelete={async () => {
              await deleteDiscordToken.mutateAsync();
              addAlert({
                message: "Discord Bot Token cleared.",
                severity: "success",
              });
            }}
            isSaving={setDiscordToken.isPending}
            isDeleting={deleteDiscordToken.isPending}
          />

          <SingleCredentialCard
            label="Polygon API Key"
            isSet={settings?.polygon_api_key_set ?? false}
            inputLabel="API Key"
            onSave={async (value) => {
              await setPolygonKey.mutateAsync(value);
              addAlert({
                message: "Polygon API Key saved.",
                severity: "success",
              });
            }}
            onDelete={async () => {
              await deletePolygonKey.mutateAsync();
              addAlert({
                message: "Polygon API Key cleared.",
                severity: "success",
              });
            }}
            isSaving={setPolygonKey.isPending}
            isDeleting={deletePolygonKey.isPending}
          />

          <ThetaDataCard
            isSet={settings?.theta_data_set ?? false}
            onSave={async (username, password) => {
              await setThetaData.mutateAsync({ username, password });
              addAlert({
                message: "ThetaData credentials saved.",
                severity: "success",
              });
            }}
            onDelete={async () => {
              await deleteThetaData.mutateAsync();
              addAlert({
                message: "ThetaData credentials cleared.",
                severity: "success",
              });
            }}
            isSaving={setThetaData.isPending}
            isDeleting={deleteThetaData.isPending}
          />

          <h2 className="text-lg font-semibold text-gray-200 pt-4">Worker Install</h2>

          <VisibleValueCard
            label="Coordinator Tailscale IP"
            value={settings?.coordinator_ip ?? null}
            inputLabel="Coordinator's Tailscale IP"
            placeholder="100.x.y.z"
            help="The coordinator's Tailscale IP (copy/paste from the Tailscale admin console). The worker install command targets http://<ip>:8000."
            onSave={async (value) => {
              await setCoordinatorIp.mutateAsync(value);
              addAlert({ message: "Coordinator IP saved.", severity: "success" });
            }}
            onDelete={async () => {
              await deleteCoordinatorIp.mutateAsync();
              addAlert({ message: "Coordinator IP cleared.", severity: "success" });
            }}
            isSaving={setCoordinatorIp.isPending}
            isDeleting={deleteCoordinatorIp.isPending}
          />

          <SingleCredentialCard
            label="Tailscale Auth Key"
            isSet={settings?.tailscale_authkey_set ?? false}
            inputLabel="Reusable Tailscale auth key (tskey-…)"
            helpLink={{
              href: "https://login.tailscale.com/admin/settings/keys",
              label: "Generate in Tailscale admin",
            }}
            onSave={async (value) => {
              await setTailscaleAuthkey.mutateAsync(value);
              addAlert({ message: "Tailscale auth key saved.", severity: "success" });
            }}
            onDelete={async () => {
              await deleteTailscaleAuthkey.mutateAsync();
              addAlert({ message: "Tailscale auth key cleared.", severity: "success" });
            }}
            isSaving={setTailscaleAuthkey.isPending}
            isDeleting={deleteTailscaleAuthkey.isPending}
          />
        </div>
      )}
    </div>
  );
}
