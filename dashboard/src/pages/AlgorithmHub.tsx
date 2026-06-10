import { useParams } from "react-router-dom";
import { useAlgorithm } from "../api/hooks";
import { AlgorithmKpiRow } from "../components/AlgorithmKpiRow";

export function AlgorithmHub() {
  const { id = "" } = useParams<{ id: string }>();
  const { data: algo } = useAlgorithm(id);
  if (!algo) return null;
  const s = algo.summary;

  return (
    <div className="space-y-8">
      <AlgorithmKpiRow
        sharpe={s?.headline_sharpe ?? null}
        sharpeSource={s?.headline_sharpe_source ?? null}
      />
      <section>
        <h2 className="mb-2 text-lg font-medium text-gray-100">Recent backtests</h2>
        <div data-testid="recent-backtests-placeholder" className="text-sm text-gray-500">
          loading…
        </div>
      </section>
      <section>
        <h2 className="mb-2 text-lg font-medium text-gray-100">Active research</h2>
        <div data-testid="active-research-placeholder" className="text-sm text-gray-500">
          loading…
        </div>
      </section>
      <section>
        <h2 className="mb-2 text-lg font-medium text-gray-100">Live deployments</h2>
        <div data-testid="live-deployments-placeholder" className="text-sm text-gray-500">
          loading…
        </div>
      </section>
    </div>
  );
}
