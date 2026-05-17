import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route, Navigate, useParams } from "react-router-dom";
import { AlertToast } from "./components/AlertToast";
import { Layout } from "./components/Layout";
import { useWebSocketSync } from "./hooks/useWebSocketSync";
import { Overview } from "./pages/Overview";
import { Accounts } from "./pages/Accounts";
import { AccountDetail } from "./pages/AccountDetail";
import { Algorithms } from "./pages/Algorithms";
import { AlgorithmDetail } from "./pages/AlgorithmDetail";
import { DeploymentDetail } from "./pages/DeploymentDetail";
import { RunDetail } from "./pages/RunDetail";
import { Workers } from "./pages/Workers";
import { WorkerDetail } from "./pages/WorkerDetail";
import { Data } from "./pages/Data";
import { Backtests } from "./pages/Backtests";
import { BacktestDetail } from "./pages/BacktestDetail";
// ── Spec D U2: backtest run detail ──
import { BacktestRunDetail } from "./pages/BacktestRunDetail";
import { Notifications } from "./pages/Notifications";
import { Settings } from "./pages/Settings";
import { Strategies } from "./pages/Strategies";

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
            {/* U6: Options strategy builder */}
            <Route path="/accounts/:id/strategies" element={<Strategies />} />
            <Route path="/algorithms" element={<Algorithms />} />
            <Route path="/algorithms/:id" element={<AlgorithmDetail />} />
            <Route path="/deployments/:id" element={<DeploymentDetail />} />
            <Route path="/instances/:id" element={<InstanceRedirect />} />
            <Route path="/runs/:id" element={<RunDetail />} />
            <Route path="/workers" element={<Workers />} />
            <Route path="/workers/:id" element={<WorkerDetail />} />
            <Route path="/data" element={<Data />} />
            <Route path="/backtests" element={<Backtests />} />
            <Route path="/backtests/:id" element={<BacktestDetail />} />
            {/* ── Spec D U2: backtest run detail ── */}
            <Route path="/backtest-runs/:id" element={<BacktestRunDetail />} />
            <Route path="/notifications" element={<Notifications />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </Layout>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
