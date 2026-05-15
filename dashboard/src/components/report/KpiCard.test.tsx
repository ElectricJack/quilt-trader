import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { KpiCard } from "./KpiCard";

describe("KpiCard", () => {
  it("renders label and value", () => {
    render(<KpiCard label="CAGR" value="32.4%" />);
    expect(screen.getByText("CAGR")).toBeInTheDocument();
    expect(screen.getByText("32.4%")).toBeInTheDocument();
  });

  it("renders hero variant with larger styling", () => {
    const { container } = render(<KpiCard label="CAGR" value="32.4%" variant="hero" />);
    expect(container.querySelector(".text-3xl, .text-4xl")).toBeTruthy();
  });
});
