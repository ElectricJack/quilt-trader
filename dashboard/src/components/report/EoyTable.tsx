interface EoyRow {
  year: number;
  strategy_pct: number;
  benchmark_pct: number | null;
  multiplier: number | null;
  won: boolean;
}

interface Props { rows: EoyRow[] | null; }

export function EoyTable({ rows }: Props) {
  if (!rows || rows.length === 0) return null;
  const fmt = (v: number | null) => (v === null ? "—" : `${v.toFixed(2)}%`);
  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-3">
      <h3 className="text-sm font-semibold text-gray-300 mb-2">EOY Returns vs Benchmark</h3>
      <table className="w-full text-xs">
        <thead className="text-gray-500">
          <tr>
            <th className="text-left py-1">Year</th>
            <th className="text-right py-1">Strategy</th>
            <th className="text-right py-1">Benchmark</th>
            <th className="text-right py-1">×</th>
            <th className="text-right py-1">Won</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.year} className="border-t border-gray-800">
              <td className="py-1 text-gray-300">{r.year}</td>
              <td className="py-1 text-right text-gray-300">{fmt(r.strategy_pct)}</td>
              <td className="py-1 text-right text-gray-400">{fmt(r.benchmark_pct)}</td>
              <td className="py-1 text-right text-gray-400">{r.multiplier === null ? "—" : r.multiplier.toFixed(2)}</td>
              <td className={`py-1 text-right ${r.won ? "text-green-400" : "text-red-400"}`}>
                {r.won ? "+" : "−"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
