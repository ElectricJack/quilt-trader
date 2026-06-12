import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { CpcvPathDetail } from "./CpcvPathDetail";

const path = [
  { group: 0, split: 3, run_id: "run-a" },
  { group: 1, split: 5, run_id: "run-b" },
  { group: 2, split: 1, run_id: "run-c" },
  { group: 3, split: 2, run_id: "run-d" },
];

describe("CpcvPathDetail", () => {
  it("renders one row per segment with link to backtest", () => {
    render(
      <MemoryRouter>
        <CpcvPathDetail
          algorithmId="a-1"
          path={path}
          pathIndex={0}
          pathSharpe={1.2}
        />
      </MemoryRouter>,
    );
    expect(screen.getAllByRole("link").length).toBe(4);
    expect(screen.getByText(/path 0/i)).toBeInTheDocument();
    expect(screen.getByText(/1\.20/)).toBeInTheDocument();
  });
});
