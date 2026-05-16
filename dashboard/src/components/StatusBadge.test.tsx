import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { StatusBadge } from "./StatusBadge";

describe("StatusBadge", () => {
  it.each([
    ["stopped", "Stopped"],
    ["starting", "Starting"],
    ["running", "Running"],
    ["stopping", "Stopping"],
    ["error", "Error"],
    ["offline", "Offline"],
    ["online", "Online"],
  ])("renders %s as %s", (status, label) => {
    render(<StatusBadge status={status} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("falls back to a neutral pill for unknown statuses", () => {
    render(<StatusBadge status="something_weird" />);
    expect(screen.getByText("something_weird")).toBeInTheDocument();
  });
});
