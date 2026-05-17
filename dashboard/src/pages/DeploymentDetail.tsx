import { useParams, Link } from "react-router-dom";
import { ChevronLeft } from "lucide-react";
import {
  useDeployment,
  useDeploymentReport,
  useStartDeployment,
  useStopDeployment,
} from "../api/hooks";
import { StatusBadge } from "../components/StatusBadge";
import { useUIStore } from "../stores/ui";
import { KpiCard } from "../components/report/KpiCard";
import { EquitySlot } from "../components/report/EquitySlot";
import { DrawdownSlot } from "../components/report/DrawdownSlot";
import { ReturnsDistributionSlot } from "../components/report/ReturnsDistributionSlot";
import { RollingMetricsSlot } from "../components/report/RollingMetricsSlot";
import { fmtPct, fmtInt, fmtNum } from "../lib/formatNumbers";

export function DeploymentDetail() {
  const { id = "" } = useParams<{ id: string }>();
  const { data: dep, isLoading } = useDeployment(id);
  const start = useStartDeployment();
  const stop = useStopDeployment();
  const addAlert = useUIStore((s) => s.addAlert);

  if (isLoading) return <p className="text-gray-400 text-sm">Loading…</p>;
  if (!dep) return <p className="text-gray-400 text-sm">Deployment not found.</p>;

  const canStart = dep.status === "stopped" || dep.status === "error";
  const isRunning = dep.status === "running" || dep.status === "starting";
  const isLive = dep.status === "running" || dep.status === "starting" || dep.status === "stopping";
  const { data: report } = useDeploymentReport(id, { refetchInterval: isLive ? 2000 : false });
  const km = report?.key_metrics?.strategy;

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3 flex-wrap">
        <Link
          to={`/algorithms/${dep.algorithm_id}`}
          className="flex items-center gap-1 text-sm text-gray-400 hover:text-gray-200 transition-colors"
        >
          <ChevronLeft size={16} />
          {dep.algorithm_name}
        </Link>

        <div className="flex items-center gap-3 flex-1 min-w-0">
          <h1 className="text-2xl font-bold text-white truncate">
            {dep.algorithm_name}
          </h1>
          <StatusBadge status={dep.status} />
        </div>

        <div className="text-sm text-gray-400">
          <Link
            to={`/accounts/${dep.account_id}`}
            className="hover:text-gray-200"
          >
            {dep.account_name}
          </Link>
          {" · "}
          <Link
            to={`/workers/${dep.worker_id}`}
            className="hover:text-gray-200"
          >
            {dep.worker_name}
          </Link>
        </div>

        <div className="flex gap-2 ml-auto">
          {canStart && (
            <button
              onClick={() =>
                start.mutate(id, {
                  onError: () =>
                    addAlert({
                      message: "Failed to start deployment.",
                      severity: "error",
                    }),
                })
              }
              disabled={start.isPending}
              className="bg-green-600 hover:bg-green-500 disabled:opacity-50 text-white text-sm px-3 py-1.5 rounded"
            >
              Start
            </button>
          )}
          {isRunning && (
            <button
              onClick={() =>
                stop.mutate(id, {
                  onError: () =>
                    addAlert({
                      message: "Failed to stop deployment.",
                      severity: "error",
                    }),
                })
              }
              disabled={stop.isPending}
              className="bg-red-600 hover:bg-red-500 disabled:opacity-50 text-white text-sm px-3 py-1.5 rounded"
            >
              Stop
            </button>
          )}
        </div>
      </div>

      {report ? (
        <>
          {km && (
            <div className="grid grid-cols-1 md:grid-cols-7 gap-3">
              <KpiCard variant="hero" label="Annual Return" value={fmtPct(km.cagr)} hint="CAGR" />
              <KpiCard label="Total Return" value={fmtPct(km.total_return)} />
              <KpiCard label="Max Drawdown" value={fmtPct(km.max_drawdown)} />
              <KpiCard label="RoMaD" value={fmtNum(km.romad)} hint="CAGR / Max Drawdown" />
              <KpiCard label="Sharpe" value={fmtNum(km.sharpe_ratio)} />
              <KpiCard label="Sortino" value={fmtNum(km.sortino_ratio)} />
              <KpiCard label="Longest DD Days" value={fmtInt(km.longest_drawdown_days)} />
            </div>
          )}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
            <EquitySlot report={report as any} trades={[]} runsIndex={report.runs_index ?? undefined} />
            {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
            <DrawdownSlot report={report as any} />
            {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
            <ReturnsDistributionSlot report={report as any} />
            {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
            <RollingMetricsSlot report={report as any} />
          </div>
        </>
      ) : (
        <p className="text-gray-500 text-sm">
          No samples yet — start the deployment to begin recording.
        </p>
      )}
    </div>
  );
}
