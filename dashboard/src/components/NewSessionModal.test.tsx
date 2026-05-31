import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { NewSessionModal } from "./NewSessionModal";

vi.mock("../api/client", () => ({
  api: {
    createResearchSession: vi.fn().mockResolvedValue({
      id: 42, name: "T", hypothesis: "H", status: "open",
      notes: "", created_at: "2026-05-30",
      completed_at: null, parameter_space: {}, pre_registered_criteria: {},
      n_runs: 0,
    }),
  },
}));

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

describe("NewSessionModal", () => {
  it("submit disabled until all required fields valid", () => {
    render(wrap(<NewSessionModal open={true} onClose={() => {}} onCreated={() => {}} />));
    const submit = screen.getByRole("button", { name: /create session/i });
    expect(submit).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/^name/i), { target: { value: "T" } });
    fireEvent.change(screen.getByLabelText(/hypothesis/i), { target: { value: "H" } });
    // parameter_space + criteria still empty → invalid
    expect(submit).toBeDisabled();
  });

  it("invalid JSON in parameter_space keeps submit disabled and shows error", async () => {
    vi.useFakeTimers();
    render(wrap(<NewSessionModal open={true} onClose={() => {}} onCreated={() => {}} />));
    const params = screen.getByLabelText(/parameter space/i);
    fireEvent.change(params, { target: { value: "{not json" } });
    act(() => vi.advanceTimersByTime(250));
    expect(screen.getByText(/invalid|expected|json/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /create session/i })).toBeDisabled();
    vi.useRealTimers();
  });

  it("successful submit calls API with correct body and invokes onCreated", async () => {
    vi.useFakeTimers();
    const onCreated = vi.fn();
    render(wrap(<NewSessionModal open={true} onClose={() => {}} onCreated={onCreated} />));
    fireEvent.change(screen.getByLabelText(/^name/i), { target: { value: "Smoke" } });
    fireEvent.change(screen.getByLabelText(/hypothesis/i), { target: { value: "test" } });
    fireEvent.change(screen.getByLabelText(/parameter space/i), {
      target: { value: '{"x":[1]}' },
    });
    fireEvent.change(screen.getByLabelText(/criteria/i), {
      target: { value: '{"min_sharpe":1}' },
    });
    act(() => vi.advanceTimersByTime(250));
    vi.useRealTimers();
    const submit = screen.getByRole("button", { name: /create session/i });
    expect(submit).not.toBeDisabled();
    fireEvent.click(submit);
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(42));
    const { api } = await import("../api/client");
    expect(api.createResearchSession).toHaveBeenCalledWith({
      name: "Smoke",
      hypothesis: "test",
      parameter_space: { x: [1] },
      pre_registered_criteria: { min_sharpe: 1 },
      notes: "",
    });
  });
});
