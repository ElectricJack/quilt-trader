const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

interface Props {
  matrix: { years: number[]; cells: [number, number, number][] } | null;
}

function colorFor(pct: number): string {
  // pct is fraction, e.g. 0.05 = +5%
  const p = Math.max(-0.10, Math.min(0.10, pct));
  if (p > 0) {
    const intensity = Math.round((p / 0.10) * 200) + 30;
    return `rgb(0, ${intensity}, 0)`;
  }
  if (p < 0) {
    const intensity = Math.round((-p / 0.10) * 200) + 30;
    return `rgb(${intensity}, 0, 0)`;
  }
  return "rgb(40, 40, 40)";
}

export function MonthlyHeatmap({ matrix }: Props) {
  if (!matrix || matrix.years.length === 0) {
    return <div className="text-xs text-gray-500 p-4">No monthly data.</div>;
  }
  const lookup = new Map<string, number>();
  for (const [y, m, v] of matrix.cells) lookup.set(`${y}-${m}`, v);
  return (
    <div className="overflow-auto">
      <table className="text-[10px] border-separate" style={{ borderSpacing: 1 }}>
        <thead>
          <tr>
            <th className="text-gray-500 px-1 text-left">Year</th>
            {MONTHS.map((m) => (
              <th key={m} className="text-gray-500 px-1">{m}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {matrix.years.map((y) => (
            <tr key={y}>
              <td className="text-gray-400 px-1">{y}</td>
              {MONTHS.map((_, idx) => {
                const v = lookup.get(`${y}-${idx + 1}`);
                if (v === undefined) {
                  return <td key={idx} className="w-8 h-6 bg-gray-800/30" />;
                }
                return (
                  <td
                    key={idx}
                    className="w-8 h-6 text-center text-white"
                    style={{ background: colorFor(v) }}
                    title={`${y}-${String(idx + 1).padStart(2, "0")}: ${(v * 100).toFixed(2)}%`}
                  >
                    {(v * 100).toFixed(0)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
