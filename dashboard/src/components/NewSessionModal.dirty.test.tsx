import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { NewSessionModal } from "./NewSessionModal";

vi.mock("../api/hooks", async () => {
  const actual = await vi.importActual<object>("../api/hooks");
  return {
    ...actual,
    useAlgorithms: () => ({ data: [] }),
    useCostProfiles: () => ({ data: ["default"] }),
  };
});
vi.mock("../hooks/useResearchMutations", () => ({
  useCreateResearchSession: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

function renderModal(onClose: () => void) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <NewSessionModal open onClose={onClose} onCreated={() => {}} />
    </QueryClientProvider>,
  );
}

function backdrop(container: HTMLElement): HTMLElement {
  return container.querySelector('[aria-hidden="true"]') as HTMLElement;
}

describe("NewSessionModal backdrop guard", () => {
  it("closes on backdrop click while the form is pristine", () => {
    const onClose = vi.fn();
    const { container } = renderModal(onClose);
    fireEvent.click(backdrop(container));
    expect(onClose).toHaveBeenCalled();
  });

  it("ignores backdrop clicks once the form is dirty", () => {
    const onClose = vi.fn();
    const { container } = renderModal(onClose);
    fireEvent.change(screen.getByLabelText(/name/i), { target: { value: "my session" } });
    fireEvent.click(backdrop(container));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("the Cancel button still closes a dirty form", () => {
    const onClose = vi.fn();
    renderModal(onClose);
    fireEvent.change(screen.getByLabelText(/name/i), { target: { value: "my session" } });
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onClose).toHaveBeenCalled();
  });
});
