import { Link } from "react-router-dom";
import { useAccounts } from "../api/hooks";

export function Accounts() {
  const { data: accounts, isLoading } = useAccounts();

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold text-white">Accounts</h1>

      {isLoading ? (
        <p className="text-gray-400 text-sm">Loading…</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left text-gray-300">
            <thead className="text-xs text-gray-400 uppercase border-b border-gray-800">
              <tr>
                <th className="py-3 px-4">Name</th>
                <th className="py-3 px-4">Broker</th>
                <th className="py-3 px-4">Assets</th>
                <th className="py-3 px-4">PDT Mode</th>
                <th className="py-3 px-4">Status</th>
              </tr>
            </thead>
            <tbody>
              {(accounts ?? []).map((a) => (
                <tr
                  key={a.id}
                  className="border-b border-gray-800 hover:bg-gray-900"
                >
                  <td className="py-3 px-4">
                    <Link
                      to={`/accounts/${a.id}`}
                      className="text-indigo-400 hover:underline"
                    >
                      {a.name}
                    </Link>
                  </td>
                  <td className="py-3 px-4">{a.broker_type}</td>
                  <td className="py-3 px-4">
                    {(a.supported_asset_types ?? []).join(", ")}
                  </td>
                  <td className="py-3 px-4">{a.pdt_mode}</td>
                  <td className="py-3 px-4">
                    {a.locked_by ? "Locked" : "Available"}
                  </td>
                </tr>
              ))}
              {(accounts ?? []).length === 0 && (
                <tr>
                  <td colSpan={5} className="py-6 text-center text-gray-500">
                    No accounts found.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
