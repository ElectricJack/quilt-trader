interface DrawdownRow {
  start: string; trough: string; recovered: string | null;
  depth: number; days: number;
}

interface Props { rows: DrawdownRow[] | null; }

export function DrawdownsTable({ rows }: Props) {
  if (!rows || rows.length === 0) return null;
  const fmtDate = (s: string | null) => (s ? s.split("T")[0] : "ongoing");
  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-3">
      <h3 className="text-sm font-semibold text-gray-300 mb-2">Worst-{rows.length} Drawdowns</h3>
      <table className="w-full text-xs">
        <thead className="text-gray-500">
          <tr>
            <th className="text-left py-1">Started</th>
            <th className="text-left py-1">Recovered</th>
            <th className="text-right py-1">Depth</th>
            <th className="text-right py-1">Days</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-gray-800">
              <td className="py-1 text-gray-300 font-mono">{fmtDate(r.start)}</td>
              <td className="py-1 text-gray-400 font-mono">{fmtDate(r.recovered)}</td>
              <td className="py-1 text-right text-red-400">{(r.depth * 100).toFixed(2)}%</td>
              <td className="py-1 text-right text-gray-400">{r.days}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
