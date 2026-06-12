import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { NewSweepModal } from "./NewSweepModal";

const sweepFn = vi.fn().mockResolvedValue({
  job_id: "j1", session_id: 7, kind: "sweep", status: "queued",
  progress_pct: 0, progress_message: null, run_ids: [],
  error_message: null, started_at: null, completed_at: null, created_at: null,
});

vi.mock("../api/client", () => ({
  api: {
    createResearchSweep: (sessionId: number, body: unknown) =>
      sweepFn(sessionId, body),
  },
}));

const addAlert = vi.fn();
vi.mock("../stores/ui", () => ({
  useUIStore: (sel: (s: { addAlert: typeof addAlert }) => unknown) => sel({ addAlert }),
}));

function renderModal(open = true) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <NewSweepModal open={open} sessionId={7} onClose={() => {}} />
    </QueryClientProvider>,
  );
}

describe("NewSweepModal", () => {
  beforeEach(() => { sweepFn.mockClear(); addAlert.mockClear(); });

  it("renders only the 4 execution fields (no algorithm/base_config/parameter_space)", () => {
    renderModal();
    expect(screen.getByLabelText(/search/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/max trials/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/parallelism/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/seed/i)).toBeInTheDocument();
    expect(screen.queryByLabelText(/^algorithm/i)).toBeNull();
    expect(screen.queryByLabelText(/base config/i)).toBeNull();
    expect(screen.queryByLabelText(/parameter space/i)).toBeNull();
  });

  it("submit body has only execution params", async () => {
    renderModal();
    fireEvent.change(screen.getByLabelText(/max trials/i), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: /start sweep/i }));
    await waitFor(() => expect(sweepFn).toHaveBeenCalled());
    const [sessionId, body] = sweepFn.mock.calls[0];
    expect(sessionId).toBe(7);
    expect(body.search).toBe("grid");
    expect(body.max_trials).toBe(10);
    expect(body.parallelism).toBe(1);
    expect(body.seed).toBe(0);
    expect(body.algorithm_id).toBeUndefined();
    expect(body.base_config).toBeUndefined();
    expect(body.parameter_space).toBeUndefined();
    expect(body.manifest_path).toBeUndefined();
  });

  it("search-strategy select reflects selection", async () => {
    renderModal();
    fireEvent.change(screen.getByLabelText(/search/i), { target: { value: "tpe" } });
    fireEvent.click(screen.getByRole("button", { name: /start sweep/i }));
    await waitFor(() => expect(sweepFn).toHaveBeenCalled());
    expect(sweepFn.mock.calls[0][1].search).toBe("tpe");
  });

  it("shows a success toast after the sweep is queued", async () => {
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: /start sweep/i }));
    await waitFor(() =>
      expect(addAlert).toHaveBeenCalledWith(
        expect.objectContaining({ severity: "success" }),
      ),
    );
  });
});
