import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { Research } from "./Research";

vi.mock("../api/client", () => ({
  api: {
    listResearchSessions: vi.fn(),
    createResearchSession: vi.fn(),
  },
}));

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={qc}>
      <BrowserRouter>{ui}</BrowserRouter>
    </QueryClientProvider>
  );
}

describe("Research", () => {
  it("renders empty state when API returns no sessions", async () => {
    const { api } = await import("../api/client");
    (api.listResearchSessions as any).mockResolvedValue([]);
    render(wrap(<Research />));
    await waitFor(() => {
      expect(screen.getByText(/create your first session/i)).toBeInTheDocument();
    });
  });

  it("renders one row per session", async () => {
    const { api } = await import("../api/client");
    (api.listResearchSessions as any).mockResolvedValue([
      { id: 1, name: "S1", hypothesis: "H1", status: "open", notes: "",
        created_at: "2026-05-30", completed_at: null, parameter_space: {},
        pre_registered_criteria: {}, n_runs: 0, algorithm_id: "algo-abc", base_config: {} },
      { id: 2, name: "S2", hypothesis: "H2", status: "running", notes: "",
        created_at: "2026-05-30", completed_at: null, parameter_space: {},
        pre_registered_criteria: {}, n_runs: 3, algorithm_id: "algo-def", base_config: {} },
    ]);
    render(wrap(<Research />));
    await waitFor(() => {
      expect(screen.getByText("S1")).toBeInTheDocument();
      expect(screen.getByText("S2")).toBeInTheDocument();
    });
  });

  it("New Session button opens the modal", async () => {
    const { api } = await import("../api/client");
    (api.listResearchSessions as any).mockResolvedValue([]);
    render(wrap(<Research />));
    fireEvent.click(screen.getByRole("button", { name: /new session/i }));
    await waitFor(() => {
      expect(screen.getByText(/new research session/i)).toBeInTheDocument();
    });
  });

  it("renders Algorithm column", async () => {
    const { api } = await import("../api/client");
    (api.listResearchSessions as any).mockResolvedValue([
      { id: 1, name: "S1", hypothesis: "H1", status: "open", notes: "",
        created_at: "2026-05-30", completed_at: null,
        algorithm_id: "algo-abc", base_config: {},
        parameter_space: {}, pre_registered_criteria: {}, n_runs: 0 },
    ]);
    render(wrap(<Research />));
    await waitFor(() => {
      expect(screen.getByText(/algo-abc/)).toBeInTheDocument();
    });
  });
});
