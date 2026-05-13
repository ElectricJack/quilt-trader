import { useParams } from "react-router-dom";
import { useAccount, useCashFlows } from "../api/hooks";
import { StatusBadge } from "../components/StatusBadge";

export function AccountDetail() {
  const { id } = useParams<{ id: string }>();
  const { data: account, isLoading: loadingAccount } = useAccount(id ?? "");
  const { data: cashFlows, isLoading: loadingCashFlows } = useCashFlows(id ?? "");

  if (loadingAccount) {
    return <p className="text-gray-400 text-sm">Loading…</p>;
  }

  if (!account) {
    return <p className="text-gray-400 text-sm">Account not found.</p>;
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-white">{account.name}</h1>

      {/* Account Info */}
      <div className="bg-gray-900 border border-gray-800 rounded p-4 space-y-3">
        <h2 className="text-lg font-semibold text-gray-200">Account Details</h2>
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <span className="text-xs text-gray-500 uppercase">Broker</span>
            <p className="text-gray-200 mt-0.5">{account.broker_type}</p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">PDT Mode</span>
            <p className="text-gray-200 mt-0.5">{account.pdt_mode}</p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Asset Types</span>
            <p className="text-gray-200 mt-0.5">
              {(account.supported_asset_types ?? []).join(", ") || "—"}
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Options Level</span>
            <p className="text-gray-200 mt-0.5">
              {account.options_level !== null ? account.options_level : "—"}
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Features</span>
            <p className="text-gray-200 mt-0.5">
              {(account.account_features ?? []).join(", ") || "—"}
            </p>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase">Status</span>
            <div className="mt-0.5">
              <StatusBadge status={account.locked_by ? "locked" : "available"} />
            </div>
          </div>
        </div>
        {account.locked_by && (
          <p className="text-xs text-gray-500">Locked by: {account.locked_by}</p>
        )}
      </div>

      {/* Cash Flows */}
      <section>
        <h2 className="text-lg font-semibold text-gray-200 mb-3">Cash Flows</h2>
        {loadingCashFlows ? (
          <p className="text-gray-400 text-sm">Loading…</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left text-gray-300">
              <thead className="text-xs text-gray-400 uppercase border-b border-gray-800">
                <tr>
                  <th className="py-3 px-4">Date</th>
                  <th className="py-3 px-4">Type</th>
                  <th className="py-3 px-4">Amount</th>
                  <th className="py-3 px-4">Notes</th>
                </tr>
              </thead>
              <tbody>
                {(cashFlows ?? []).map((cf) => (
                  <tr key={cf.id} className="border-b border-gray-800 hover:bg-gray-900">
                    <td className="py-3 px-4 text-xs text-gray-400">
                      {cf.timestamp ? new Date(cf.timestamp).toLocaleString() : "—"}
                    </td>
                    <td className="py-3 px-4">{cf.type}</td>
                    <td className="py-3 px-4">
                      <span className={cf.amount >= 0 ? "text-green-400" : "text-red-400"}>
                        {cf.amount >= 0 ? "+" : ""}
                        {cf.amount.toFixed(2)}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-xs text-gray-500">{cf.notes ?? "—"}</td>
                  </tr>
                ))}
                {(cashFlows ?? []).length === 0 && (
                  <tr>
                    <td colSpan={4} className="py-6 text-center text-gray-500">
                      No cash flows recorded.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
