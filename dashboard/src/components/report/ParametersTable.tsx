interface Props {
  params: Record<string, unknown> | null;
}

export function ParametersTable({ params }: Props) {
  const entries = params ? Object.entries(params) : [];
  if (entries.length === 0) return null;
  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-3">
      <h3 className="text-sm font-semibold text-gray-300 mb-2">Parameters Used</h3>
      <table className="w-full text-xs">
        <thead className="text-gray-500">
          <tr><th className="text-left py-1">Parameter</th><th className="text-right py-1">Value</th></tr>
        </thead>
        <tbody>
          {entries.map(([k, v]) => (
            <tr key={k} className="border-t border-gray-800">
              <td className="py-1 text-gray-300 font-mono">{k}</td>
              <td className="py-1 text-right text-gray-300 font-mono">
                {v === null || v === undefined ? "—" : String(v)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
