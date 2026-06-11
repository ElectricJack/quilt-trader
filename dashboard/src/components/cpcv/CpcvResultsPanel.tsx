import { useParams } from "react-router-dom";
import type { CPCVResult } from "../../types";
import { CpcvSummaryCard } from "./CpcvSummaryCard";
import { CpcvPathDistribution } from "./CpcvPathDistribution";
import { CpcvSegmentHeatmap } from "./CpcvSegmentHeatmap";
import { CpcvPathDetail } from "./CpcvPathDetail";

interface Props {
  result: CPCVResult | null;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  progress: { pct: number; message: string } | null;
}

export function CpcvResultsPanel({ result, status, progress }: Props) {
  const { id: algorithmId = "" } = useParams<{ id: string }>();
  if (status === "running" || status === "queued") {
    return (
      <div className="rounded border border-gray-800 bg-gray-900 p-4">
        <div className="mb-2 text-sm text-gray-300">{progress?.message ?? "starting…"}</div>
        <div className="h-2 w-full overflow-hidden rounded bg-gray-800">
          <div
            className="h-full bg-indigo-500 transition-all"
            style={{ width: `${Math.round((progress?.pct ?? 0) * 100)}%` }}
          />
        </div>
      </div>
    );
  }
  if (!result) return <div className="text-sm text-gray-500">No result.</div>;
  const ci: [number, number] = [
    result.summary.bootstrap_ci_lower,
    result.summary.bootstrap_ci_upper,
  ];
  const showPathDistribution = result.mode === "select" && (result.summary.path_sharpes?.length ?? 0) > 0;
  return (
    <div className="space-y-4">
      <CpcvSummaryCard result={result} />
      <div className="grid gap-4 lg:grid-cols-2">
        {showPathDistribution && (
          <div className="rounded border border-gray-800 bg-gray-900 p-3">
            <h3 className="mb-2 text-sm text-gray-300">Path Sharpe distribution</h3>
            <CpcvPathDistribution
              path_sharpes={result.summary.path_sharpes!}
              mean={result.summary.mean_path_sharpe ?? 0}
              ci={ci}
            />
          </div>
        )}
        <div className={`rounded border border-gray-800 bg-gray-900 p-3 ${showPathDistribution ? "" : "lg:col-span-2"}`}>
          <h3 className="mb-2 text-sm text-gray-300">Per-segment Sharpe</h3>
          <CpcvSegmentHeatmap result={result} />
        </div>
      </div>
      {result.mode === "select" && result.paths.map((path, i) => (
        <CpcvPathDetail
          key={i}
          algorithmId={algorithmId}
          path={path}
          pathIndex={i}
          pathSharpe={result.summary.path_sharpes?.[i] ?? 0}
        />
      ))}
    </div>
  );
}
