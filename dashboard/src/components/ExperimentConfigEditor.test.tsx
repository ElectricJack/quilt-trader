import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { ExperimentConfigEditor } from "./ExperimentConfigEditor";

describe("ExperimentConfigEditor", () => {
  it("renders three JsonTextField children with correct labels", () => {
    render(
      <ExperimentConfigEditor
        baseConfig={{}}
        parameterSpace={{}}
        criteria={{}}
        onChange={() => {}}
        onValidityChange={() => {}}
      />,
    );
    expect(screen.getByLabelText(/base config/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/parameter space/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/criteria/i)).toBeInTheDocument();
  });

  it("emits combined object on base_config change", async () => {
    vi.useFakeTimers();
    const onChange = vi.fn();
    render(
      <ExperimentConfigEditor
        baseConfig={{}}
        parameterSpace={{ "x": [1] }}
        criteria={{ "min_sharpe": 1 }}
        onChange={onChange}
        onValidityChange={() => {}}
      />,
    );
    const base = screen.getByLabelText(/base config/i);
    fireEvent.change(base, { target: { value: '{"vol":0.1}' } });
    act(() => vi.advanceTimersByTime(250));
    expect(onChange).toHaveBeenCalledWith({
      base_config: { vol: 0.1 },
      parameter_space: { x: [1] },
      pre_registered_criteria: { min_sharpe: 1 },
    });
    vi.useRealTimers();
  });

  it("disabled propagates to all three children", () => {
    render(
      <ExperimentConfigEditor
        baseConfig={{}}
        parameterSpace={{}}
        criteria={{}}
        onChange={() => {}}
        onValidityChange={() => {}}
        disabled
      />,
    );
    expect(screen.getByLabelText(/base config/i)).toBeDisabled();
    expect(screen.getByLabelText(/parameter space/i)).toBeDisabled();
    expect(screen.getByLabelText(/criteria/i)).toBeDisabled();
  });

  it("onValidityChange fires false when any field has invalid JSON", async () => {
    vi.useFakeTimers();
    const onValidityChange = vi.fn();
    render(
      <ExperimentConfigEditor
        baseConfig={{}}
        parameterSpace={{}}
        criteria={{}}
        onChange={() => {}}
        onValidityChange={onValidityChange}
      />,
    );
    const ps = screen.getByLabelText(/parameter space/i);
    fireEvent.change(ps, { target: { value: "{not json" } });
    act(() => vi.advanceTimersByTime(250));
    expect(onValidityChange).toHaveBeenCalledWith(false);
    vi.useRealTimers();
  });

  it("onValidityChange fires true once all three are valid", async () => {
    vi.useFakeTimers();
    const onValidityChange = vi.fn();
    render(
      <ExperimentConfigEditor
        baseConfig={{}}
        parameterSpace={{}}
        criteria={{}}
        onChange={() => {}}
        onValidityChange={onValidityChange}
      />,
    );
    fireEvent.change(screen.getByLabelText(/base config/i), {
      target: { value: '{"a":1}' },
    });
    fireEvent.change(screen.getByLabelText(/parameter space/i), {
      target: { value: '{"x":[1]}' },
    });
    fireEvent.change(screen.getByLabelText(/criteria/i), {
      target: { value: '{"min_sharpe":1}' },
    });
    act(() => vi.advanceTimersByTime(250));
    expect(onValidityChange).toHaveBeenLastCalledWith(true);
    vi.useRealTimers();
  });
});
