import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { CpcvPathDistribution } from "./CpcvPathDistribution";

describe("CpcvPathDistribution", () => {
  it("renders strip plot when n_paths < 8", () => {
    const { container } = render(
      <CpcvPathDistribution path_sharpes={[1.1, 0.9, 1.3, 1.0, 0.8]} mean={1.02} ci={[0.7, 1.4]} />,
    );
    const circles = container.querySelectorAll("circle");
    expect(circles.length).toBe(5);
  });

  it("renders histogram when n_paths >= 8", () => {
    const vals = Array.from({ length: 20 }, (_, i) => i * 0.1);
    const { container } = render(
      <CpcvPathDistribution path_sharpes={vals} mean={1.0} ci={[0.4, 1.6]} />,
    );
    expect(container.querySelectorAll("rect").length).toBeGreaterThan(0);
  });

  it("renders mean line and zero baseline", () => {
    const { container } = render(
      <CpcvPathDistribution path_sharpes={[1.0, 1.1]} mean={1.05} ci={[0.8, 1.3]} />,
    );
    const lines = container.querySelectorAll("line");
    expect(lines.length).toBeGreaterThanOrEqual(2);
  });
});
