import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Overview } from "./pages/Overview";
import { Accounts } from "./pages/Accounts";
import { AccountDetail } from "./pages/AccountDetail";
import { Algorithms } from "./pages/Algorithms";
import { AlgorithmDetail } from "./pages/AlgorithmDetail";
import { InstanceDetail } from "./pages/InstanceDetail";
import { RunDetail } from "./pages/RunDetail";
import { Workers } from "./pages/Workers";
import { Data } from "./pages/Data";
import { Backtests } from "./pages/Backtests";
import { BacktestDetail } from "./pages/BacktestDetail";
import { Notifications } from "./pages/Notifications";
import { Settings } from "./pages/Settings";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 30_000,
    },
  },
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Layout>
          <Routes>
            <Route path="/" element={<Overview />} />
            <Route path="/accounts" element={<Accounts />} />
            <Route path="/accounts/:id" element={<AccountDetail />} />
            <Route path="/algorithms" element={<Algorithms />} />
            <Route path="/algorithms/:id" element={<AlgorithmDetail />} />
            <Route path="/instances/:id" element={<InstanceDetail />} />
            <Route path="/runs/:id" element={<RunDetail />} />
            <Route path="/workers" element={<Workers />} />
            <Route path="/data" element={<Data />} />
            <Route path="/backtests" element={<Backtests />} />
            <Route path="/backtests/:id" element={<BacktestDetail />} />
            <Route path="/notifications" element={<Notifications />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </Layout>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
