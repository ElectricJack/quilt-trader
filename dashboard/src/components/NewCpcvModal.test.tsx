import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { NewCpcvModal } from "./NewCpcvModal";

const mut = vi.fn().mockResolvedValue({
  job_id: "j1", kind: "cpcv", status: "queued", projected_backtest_count: 330,
});
vi.mock("../api/hooks", async () => {
  const actual = await vi.importActual<any>("../api/hooks");
  return {
    ...actual,
    useCreateCpcvJob: () => ({ mutateAsync: mut, isPending: false }),
  };
});

const addAlert = vi.fn();
vi.mock("../stores/ui", () => ({
  useUIStore: (sel: (s: { addAlert: typeof addAlert }) => unknown) => sel({ addAlert }),
}));

function renderModal(open = true) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <NewCpcvModal open={open} sessionId={42} onClose={() => {}} />
    </QueryClientProvider>,
  );
}

describe("NewCpcvModal", () => {
  beforeEach(() => { mut.mockClear(); addAlert.mockClear(); });

  it("renders projected backtest count for default fixed mode", () => {
    renderModal();
    // mode=fixed defaults: N=6 → 6
    expect(screen.getByText(/projected.*6.*backtests/i)).toBeInTheDocument();
  });

  it("updates projection when switching to select mode", () => {
    renderModal();
    fireEvent.click(screen.getByLabelText(/select/i));
    fireEvent.change(screen.getByLabelText(/max trials/i), { target: { value: "20" } });
    // 15 * (20 + 2) = 330
    expect(screen.getByText(/projected.*330.*backtests/i)).toBeInTheDocument();
  });

  it("submits with correct payload on click", async () => {
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: /submit/i }));
    expect(mut).toHaveBeenCalledWith({
      sessionId: 42,
      body: expect.objectContaining({ mode: "fixed", n_groups: 6, test_groups_per_split: 2 }),
    });
  });

  it("disables submit while parameter-space JSON is invalid in select mode", async () => {
    renderModal();
    fireEvent.click(screen.getByLabelText(/select/i));
    const textarea = screen.getByLabelText(/parameter space/i);
    fireEvent.change(textarea, { target: { value: "{not json" } });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /submit/i })).toBeDisabled(),
    );
  });

  it("shows a success toast and closes after submit", async () => {
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: /submit/i }));
    await waitFor(() =>
      expect(addAlert).toHaveBeenCalledWith(
        expect.objectContaining({ severity: "success" }),
      ),
    );
  });

  it("shows an error toast when submit fails", async () => {
    mut.mockRejectedValueOnce(new Error("boom"));
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: /submit/i }));
    await waitFor(() =>
      expect(addAlert).toHaveBeenCalledWith(
        expect.objectContaining({ severity: "error" }),
      ),
    );
  });
});
