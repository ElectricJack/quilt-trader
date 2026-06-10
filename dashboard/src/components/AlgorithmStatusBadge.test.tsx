import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AlgorithmStatusBadge } from "./AlgorithmStatusBadge";

describe("AlgorithmStatusBadge", () => {
  it.each([
    ["live", "bg-emerald-500/15", "text-emerald-400"],
    ["paper", "bg-amber-500/15", "text-amber-400"],
    ["idle", "bg-gray-500/15", "text-gray-400"],
  ])("renders %s with correct color classes", (status, bg, text) => {
    render(<AlgorithmStatusBadge status={status as "live" | "paper" | "idle"} />);
    const el = screen.getByText((c) => c.includes(status));
    expect(el.className).toContain(bg);
    expect(el.className).toContain(text);
  });
});
