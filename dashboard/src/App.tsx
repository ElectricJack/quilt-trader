import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route, Navigate, useParams } from "react-router-dom";
import { AlertToast } from "./components/AlertToast";
import { Layout } from "./components/Layout";
import { useWebSocketSync } from "./hooks/useWebSocketSync";
import { Overview } from "./pages/Overview";
import { Accounts } from "./pages/Accounts";
import { AccountDetail } from "./pages/AccountDetail";
import { AlgorithmsGrid } from "./pages/AlgorithmsGrid";
import { DeploymentDetail } from "./pages/DeploymentDetail";
import { Workers } from "./pages/Workers";
import { WorkerDetail } from "./pages/WorkerDetail";
import { Data } from "./pages/Data";
import { BacktestRunDetail } from "./pages/BacktestRunDetail";
import { Notifications } from "./pages/Notifications";
import { Settings } from "./pages/Settings";
import { Strategies } from "./pages/Strategies";
import { ResearchSessionDetail } from "./pages/ResearchSessionDetail";
import { AlgorithmShell } from "./components/AlgorithmShell";
import { AlgorithmHub } from "./pages/AlgorithmHub";
import { AlgorithmBacktestsList } from "./pages/AlgorithmBacktestsList";
import { AlgorithmResearchList } from "./pages/AlgorithmResearchList";
import { AlgorithmDeploymentsList } from "./pages/AlgorithmDeploymentsList";
import { AlgorithmConfig } from "./pages/AlgorithmConfig";
import { LegacyBacktestRunRedirect } from "./pages/redirects/LegacyBacktestRunRedirect";
import { LegacyResearchSessionRedirect } from "./pages/redirects/LegacyResearchSessionRedirect";
import { LegacyDeploymentRedirect } from "./pages/redirects/LegacyDeploymentRedirect";
import { LegacyBacktestsCompareRedirect } from "./pages/redirects/LegacyBacktestsCompareRedirect";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 30_000,
    },
  },
});

function InstanceRedirect() {
  const { id } = useParams<{ id: string }>();
  return <Navigate to={`/deployments/${id ?? ""}`} replace />;
}

function WebSocketSync(): null {
  useWebSocketSync();
  return null;
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <WebSocketSync />
      <AlertToast />
      <BrowserRouter>
        <Layout>
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/accounts" element={<Accounts />} />
            <Route path="/accounts/:id" element={<AccountDetail />} />
            {/* Unified Open Position page (equities/crypto/options).
                /strategies kept as a back-compat alias. */}
            <Route path="/accounts/:id/open-position" element={<Strategies />} />
            <Route path="/accounts/:id/strategies" element={<Strategies />} />
            <Route path="/algorithms" element={<AlgorithmsGrid />} />
            {/* Nested algorithm routes — detail components keep their existing :id param */}
            <Route path="/algorithms/:id" element={<AlgorithmShell />}>
              <Route index element={<AlgorithmHub />} />
              <Route path="backtests" element={<AlgorithmBacktestsList />} />
              <Route path="backtests/:runId" element={<BacktestRunDetail />} />
              <Route path="research" element={<AlgorithmResearchList />} />
              <Route path="research/:sessionId" element={<ResearchSessionDetail />} />
              <Route path="deployments" element={<AlgorithmDeploymentsList />} />
              <Route path="deployments/:instanceId" element={<DeploymentDetail />} />
              <Route path="config" element={<AlgorithmConfig />} />
            </Route>
            {/* Legacy URL redirects — resolve entity then navigate to nested route */}
            <Route path="/backtest-runs/:id" element={<LegacyBacktestRunRedirect />} />
            <Route path="/backtests/:id" element={<LegacyBacktestsCompareRedirect />} />
            <Route path="/backtests" element={<Navigate to="/algorithms" replace />} />
            <Route path="/research/sessions/:id" element={<LegacyResearchSessionRedirect />} />
            <Route path="/research" element={<Navigate to="/algorithms" replace />} />
            <Route path="/deployments/:id" element={<LegacyDeploymentRedirect />} />
            <Route path="/instances/:id" element={<InstanceRedirect />} />
            <Route path="/workers" element={<Workers />} />
            <Route path="/workers/:id" element={<WorkerDetail />} />
            <Route path="/data" element={<Data />} />
            <Route path="/notifications" element={<Notifications />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </Layout>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
