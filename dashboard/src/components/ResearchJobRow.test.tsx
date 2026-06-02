import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { BrowserRouter } from "react-router-dom";
import { ResearchJobRow } from "./ResearchJobRow";
import type { ResearchJob } from "../api/client";

const baseJob: ResearchJob = {
  job_id: "j-1",
  session_id: 7,
  kind: "sweep",
  status: "running",
  progress_pct: 0.42,
  progress_message: "trial 12/30",
  run_ids: ["r1", "r2", "r3"],
  error_message: null,
  started_at: "2026-05-30T12:00:00Z",
  completed_at: null,
  created_at: "2026-05-30T11:55:00Z",
};

function wrap(ui: React.ReactNode) {
  return <BrowserRouter>{ui}</BrowserRouter>;
}

describe("ResearchJobRow", () => {
  it("renders status pill and progress bar at the right width", () => {
    render(wrap(<ResearchJobRow job={baseJob} onCancel={() => {}} />));
    expect(screen.getByText(/running/i)).toBeInTheDocument();
    const bar = screen.getByTestId("progress-fill");
    expect(bar.style.width).toBe("42%");
  });

  it("Cancel button visible only when status is queued or running", () => {
    const { rerender } = render(
      wrap(<ResearchJobRow job={baseJob} onCancel={() => {}} />),
    );
    expect(screen.getByRole("button", { name: /cancel/i })).toBeInTheDocument();
    rerender(wrap(<ResearchJobRow job={{ ...baseJob, status: "completed" }} onCancel={() => {}} />));
    expect(screen.queryByRole("button", { name: /cancel/i })).toBeNull();
  });

  it("renders run_ids as links to /backtest-runs/{id}", () => {
    render(wrap(<ResearchJobRow job={baseJob} onCancel={() => {}} />));
    fireEvent.click(screen.getByText(/3 runs/i));
    const link = screen.getByRole("link", { name: /r1/ });
    expect(link).toHaveAttribute("href", "/backtest-runs/r1");
  });

  it("expanded row shows error_message when status=failed", () => {
    render(
      wrap(
        <ResearchJobRow
          job={{ ...baseJob, status: "failed", error_message: "Boom" }}
          onCancel={() => {}}
        />,
      ),
    );
    fireEvent.click(screen.getByText(/sweep/i));
    expect(screen.getByText(/boom/i)).toBeInTheDocument();
  });

  it("clicking Cancel invokes onCancel with job_id", () => {
    const onCancel = vi.fn();
    render(wrap(<ResearchJobRow job={baseJob} onCancel={onCancel} />));
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onCancel).toHaveBeenCalledWith("j-1");
  });
});
