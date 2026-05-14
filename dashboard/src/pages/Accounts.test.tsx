import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { Accounts } from "./Accounts";

// ── U1: broker asset-type checkboxes ──

// Mock the api/hooks module
vi.mock("../api/hooks", () => ({
  useAccounts: () => ({ data: [], isLoading: false }),
  useCreateAccount: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateAccount: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteAccount: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useBrokerAssetTypes: (brokerType: string | null | undefined) => ({
    data: brokerType === "alpaca" ? ["equities", "options", "crypto"] : [],
    isLoading: false,
  }),
}));

// Mock api client
vi.mock("../api/client", () => ({
  api: {
    testAccountConnection: vi.fn(),
    listAccounts: vi.fn().mockResolvedValue([]),
  },
}));

function renderWithProviders(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        {ui}
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("Accounts – U1: broker-aware asset-type checkboxes", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows asset-type checkboxes (equities, options, crypto) when Add Account modal is opened", async () => {
    renderWithProviders(<Accounts />);

    // Click the "Add Account" button
    const addButton = screen.getByRole("button", { name: /add account/i });
    fireEvent.click(addButton);

    // The modal should now show the asset type checkboxes for alpaca (default broker)
    expect(screen.getByLabelText(/equities/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/options/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/crypto/i)).toBeInTheDocument();
  });
});
