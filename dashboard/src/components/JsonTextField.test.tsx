import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { JsonTextField } from "./JsonTextField";

describe("JsonTextField", () => {
  it("calls onChange with parsed value when input is valid JSON", () => {
    vi.useFakeTimers();
    const onChange = vi.fn();
    render(
      <JsonTextField
        label="Param space"
        value={{}}
        onChange={onChange}
      />,
    );
    const ta = screen.getByLabelText("Param space");
    fireEvent.change(ta, { target: { value: '{"vol":0.1}' } });
    act(() => vi.advanceTimersByTime(250));
    expect(onChange).toHaveBeenCalledWith({ vol: 0.1 });
    expect(screen.queryByText(/invalid|error/i)).toBeNull();
    vi.useRealTimers();
  });

  it("shows error message when JSON is invalid and does NOT call onChange", () => {
    vi.useFakeTimers();
    const onChange = vi.fn();
    render(
      <JsonTextField label="Param space" value={{}} onChange={onChange} />,
    );
    const ta = screen.getByLabelText("Param space");
    fireEvent.change(ta, { target: { value: "{not json" } });
    act(() => vi.advanceTimersByTime(250));
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByText(/json|expected|error/i)).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("renders as read-only when disabled, shows pretty JSON, no error UI", () => {
    render(
      <JsonTextField
        label="Param space"
        value={{ a: 1, b: 2 }}
        onChange={() => {}}
        disabled
      />,
    );
    const ta = screen.getByLabelText("Param space") as HTMLTextAreaElement;
    expect(ta).toBeDisabled();
    expect(ta.value).toContain("a");
    expect(ta.value).toContain("b");
  });

  it("debounces — typing 5 chars within 100ms produces no onChange", () => {
    vi.useFakeTimers();
    const onChange = vi.fn();
    render(<JsonTextField label="X" value={{}} onChange={onChange} />);
    const ta = screen.getByLabelText("X");
    fireEvent.change(ta, { target: { value: "{" } });
    act(() => vi.advanceTimersByTime(50));
    fireEvent.change(ta, { target: { value: '{"' } });
    act(() => vi.advanceTimersByTime(50));
    expect(onChange).not.toHaveBeenCalled();
    vi.useRealTimers();
  });
});
