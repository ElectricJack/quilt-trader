import { Widget } from "../Widget";
import { useAccounts } from "../../api/hooks";

export function AccountBalancesWidget() {
  const { data: accounts, isLoading } = useAccounts();

  return (
    <Widget title="Account Balances" isLoading={isLoading}>
      {!accounts || accounts.length === 0 ? (
        <p className="text-gray-500 text-sm">No accounts configured</p>
      ) : (
        <ul className="space-y-2">
          {accounts.map((account) => (
            <li
              key={account.id}
              className="flex items-center justify-between py-1.5 border-b border-gray-800 last:border-b-0"
            >
              <span className="text-sm text-gray-200 font-medium truncate max-w-[140px]">
                {account.name}
              </span>
              <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-700 text-gray-400 capitalize shrink-0 ml-2">
                {account.broker_type}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Widget>
  );
}
