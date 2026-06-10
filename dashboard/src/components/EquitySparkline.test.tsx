import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { EquitySparkline } from "./EquitySparkline";

describe("EquitySparkline", () => {
  it("renders an SVG path when given points", () => {
    const { container } = render(<EquitySparkline points={[1, 2, 3, 2, 5]} />);
    const path = container.querySelector("path");
    expect(path).not.toBeNull();
    expect(path!.getAttribute("d")).toBeTruthy();
  });

  it("renders empty placeholder when points is empty", () => {
    const { container } = render(<EquitySparkline points={[]} />);
    expect(container.querySelector("path")).toBeNull();
    expect(container.textContent).toMatch(/no data/i);
  });

  it("colors green when last >= first, red otherwise", () => {
    const { container: up } = render(<EquitySparkline points={[1, 2, 3]} />);
    expect(up.querySelector("path")!.getAttribute("stroke")).toMatch(/emerald|#34d399|#10b981/);

    const { container: down } = render(<EquitySparkline points={[3, 2, 1]} />);
    expect(down.querySelector("path")!.getAttribute("stroke")).toMatch(/red|#ef4444|#f87171/);
  });
});
