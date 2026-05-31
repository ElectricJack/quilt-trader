import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
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

vi.mock("../api/hooks", async (importOriginal) => {
  const orig = await importOriginal<typeof import("../api/hooks")>();
  return {
    ...orig,
    useAlgorithms: () => ({
      data: [
        { id: "algo-a", name: "Algo A", manifest_path: "/p/algo-a/quilt.yaml" },
        { id: "algo-b", name: "Algo B", manifest_path: "/p/algo-b/quilt.yaml" },
      ],
      isLoading: false,
    }),
  };
});

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

describe("NewSweepModal", () => {
  it("algorithm dropdown is populated from useAlgorithms", () => {
    render(wrap(<NewSweepModal open={true} sessionId={7} onClose={() => {}} />));
    const sel = screen.getByLabelText(/algorithm/i);
    expect(sel).toHaveTextContent("Algo A");
    expect(sel).toHaveTextContent("Algo B");
  });

  it("submit body uses algorithm_id, not manifest_path", async () => {
    render(wrap(<NewSweepModal open={true} sessionId={7} onClose={() => {}} />));
    fireEvent.change(screen.getByLabelText(/algorithm/i), { target: { value: "algo-a" } });
    fireEvent.click(screen.getByRole("button", { name: /start sweep/i }));
    await waitFor(() => expect(sweepFn).toHaveBeenCalled());
    const [sessionId, body] = sweepFn.mock.calls[0];
    expect(sessionId).toBe(7);
    expect(body.algorithm_id).toBe("algo-a");
    expect(body.manifest_path).toBeUndefined();
  });

  it("invalid base_config disables submit", async () => {
    vi.useFakeTimers();
    render(wrap(<NewSweepModal open={true} sessionId={7} onClose={() => {}} />));
    fireEvent.change(screen.getByLabelText(/algorithm/i), { target: { value: "algo-a" } });
    fireEvent.change(screen.getByLabelText(/base config/i), { target: { value: "{not json" } });
    act(() => vi.advanceTimersByTime(250));
    expect(screen.getByRole("button", { name: /start sweep/i })).toBeDisabled();
    vi.useRealTimers();
  });

  it("empty parameter_space submits as null", async () => {
    sweepFn.mockClear();
    render(wrap(<NewSweepModal open={true} sessionId={7} onClose={() => {}} />));
    fireEvent.change(screen.getByLabelText(/algorithm/i), { target: { value: "algo-b" } });
    fireEvent.click(screen.getByRole("button", { name: /start sweep/i }));
    await waitFor(() => expect(sweepFn).toHaveBeenCalled());
    const body = sweepFn.mock.calls[0][1];
    expect(body.parameter_space).toBeNull();
  });
});
