import type { CPCVResult } from "../../types";

const CELL = 28;
const GAP = 2;
const POS = "#10b981";
const NEG = "#ef4444";

export function CpcvSegmentHeatmap({ result }: { result: CPCVResult }) {
  if (result.mode === "fixed") {
    const sharpes = result.summary.segment_sharpes ?? [];
    const maxAbs = Math.max(0.01, ...sharpes.map(Math.abs));
    const width = sharpes.length * (CELL + GAP);
    return (
      <svg width={width} height={CELL + 30}>
        {sharpes.map((sr, i) => (
          <g key={i}>
            <rect
              data-testid={`heatmap-cell-${i}-0`}
              x={i * (CELL + GAP)}
              y={20}
              width={CELL}
              height={CELL}
              fill={sr > 0 ? POS : NEG}
              fillOpacity={Math.min(1, Math.abs(sr) / maxAbs)}
            />
            <text x={i * (CELL + GAP) + CELL / 2} y={18} fill="#9ca3af" fontSize={10} textAnchor="middle">
              G{i}
            </text>
          </g>
        ))}
      </svg>
    );
  }
  // mode B: rows = groups, cols = occupancy index within that group's test memberships
  // Use split.selected_objective as cell sharpe (v1 approximation; future: read each
  // segment's BacktestRun.sharpe_ratio in a real fetch).
  const n = result.n_groups;
  const occupancyPerGroup: number[] = new Array(n).fill(0);
  type Cell = { row: number; col: number; sharpe: number };
  const cells: Cell[] = [];
  for (const s of result.splits) {
    for (const g of s.test_groups) {
      const col = occupancyPerGroup[g]++;
      cells.push({ row: g, col, sharpe: s.selected_objective ?? 0 });
    }
  }
  const cols = Math.max(...occupancyPerGroup, 1);
  const maxAbs = Math.max(0.01, ...cells.map((c) => Math.abs(c.sharpe)));
  const width = cols * (CELL + GAP) + 40;
  const height = n * (CELL + GAP) + 20;
  return (
    <svg width={width} height={height}>
      {cells.map(({ row, col, sharpe }) => (
        <rect
          key={`${row}-${col}`}
          data-testid={`heatmap-cell-${row}-${col}`}
          x={40 + col * (CELL + GAP)}
          y={row * (CELL + GAP)}
          width={CELL}
          height={CELL}
          fill={sharpe > 0 ? POS : NEG}
          fillOpacity={Math.min(1, Math.abs(sharpe) / maxAbs)}
        />
      ))}
      {Array.from({ length: n }).map((_, row) => (
        <text key={row} x={32} y={row * (CELL + GAP) + CELL * 0.7} fill="#9ca3af" fontSize={10} textAnchor="end">
          G{row}
        </text>
      ))}
    </svg>
  );
}
