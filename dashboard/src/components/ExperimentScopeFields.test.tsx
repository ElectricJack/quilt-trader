import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ExperimentScopeFields } from "./ExperimentScopeFields";

const baseProps = {
  startDate: "",
  endDate: "",
  initialCash: 10000,
  costProfile: "default",
  benchmarkSymbol: "",
  benchmarkSource: "",
  onChange: () => {},
  onValidityChange: () => {},
};

describe("ExperimentScopeFields", () => {
  it("renders 6 inputs with correct labels", () => {
    render(<ExperimentScopeFields {...baseProps} />);
    expect(screen.getByLabelText(/start date/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/end date/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/initial cash/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/cost profile/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/benchmark symbol/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/benchmark source/i)).toBeInTheDocument();
  });

  it("onChange emits combined object on field change", () => {
    const onChange = vi.fn();
    render(<ExperimentScopeFields {...baseProps} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText(/start date/i),
                     { target: { value: "2023-01-01" } });
    const last = onChange.mock.calls[onChange.mock.calls.length - 1][0];
    expect(last.date_range_start).toBe("2023-01-01");
    // unchanged fields preserved
    expect(last.initial_cash).toBe(10000);
    expect(last.cost_profile).toBe("default");
    // null benchmarks when both empty
    expect(last.benchmark_symbol).toBeNull();
    expect(last.benchmark_source).toBeNull();
  });

  it("onValidityChange false when end ≤ start", () => {
    const onValidityChange = vi.fn();
    render(
      <ExperimentScopeFields
        {...baseProps}
        startDate="2024-12-31"
        endDate="2023-01-01"
        onValidityChange={onValidityChange}
      />,
    );
    expect(onValidityChange).toHaveBeenLastCalledWith(false);
  });

  it("onValidityChange true when all required fields valid + benchmark pair empty", () => {
    const onValidityChange = vi.fn();
    render(
      <ExperimentScopeFields
        {...baseProps}
        startDate="2023-01-01"
        endDate="2024-12-31"
        onValidityChange={onValidityChange}
      />,
    );
    expect(onValidityChange).toHaveBeenLastCalledWith(true);
  });

  it("onValidityChange false when only one benchmark field is set", () => {
    const onValidityChange = vi.fn();
    render(
      <ExperimentScopeFields
        {...baseProps}
        startDate="2023-01-01"
        endDate="2024-12-31"
        benchmarkSymbol="SPY"
        benchmarkSource=""
        onValidityChange={onValidityChange}
      />,
    );
    expect(onValidityChange).toHaveBeenLastCalledWith(false);
  });

  it("disabled propagates to all 6 inputs", () => {
    render(<ExperimentScopeFields {...baseProps} disabled />);
    expect(screen.getByLabelText(/start date/i)).toBeDisabled();
    expect(screen.getByLabelText(/end date/i)).toBeDisabled();
    expect(screen.getByLabelText(/initial cash/i)).toBeDisabled();
    expect(screen.getByLabelText(/cost profile/i)).toBeDisabled();
    expect(screen.getByLabelText(/benchmark symbol/i)).toBeDisabled();
    expect(screen.getByLabelText(/benchmark source/i)).toBeDisabled();
  });
});
