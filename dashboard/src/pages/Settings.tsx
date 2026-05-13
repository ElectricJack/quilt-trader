import { useSettings } from "../api/hooks";

const SETTING_LABELS: Record<string, string> = {
  github_pat_set: "GitHub PAT",
  discord_bot_token_set: "Discord Bot Token",
  polygon_api_key_set: "Polygon API Key",
  theta_data_set: "ThetaData Credentials",
};

export function Settings() {
  const { data: settings, isLoading } = useSettings();

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-white">Settings</h1>

      {isLoading ? (
        <p className="text-gray-400 text-sm">Loading…</p>
      ) : (
        <div className="space-y-3">
          {Object.entries(SETTING_LABELS).map(([key, label]) => {
            const isSet = settings?.[key as keyof typeof settings] ?? false;
            return (
              <div
                key={key}
                className="flex items-center justify-between bg-gray-900 border border-gray-800 rounded p-4"
              >
                <span className="text-sm text-gray-200">{label}</span>
                <span
                  className={`text-xs font-medium px-2 py-0.5 rounded ${
                    isSet
                      ? "bg-green-900 text-green-300"
                      : "bg-gray-700 text-gray-400"
                  }`}
                >
                  {isSet ? "Configured" : "Not set"}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
