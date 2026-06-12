import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ResearchJobRow } from "./ResearchJobRow";
import type { ResearchJob } from "../api/client";

const job: ResearchJob = {
  job_id: "j1", session_id: 1, kind: "sweep", status: "completed",
  progress_pct: 1, progress_message: null, run_ids: [],
  error_message: null, started_at: null, completed_at: null, created_at: null,
  request_payload: { search: "grid", max_trials: 50, manifest_path: "/x/quilt.yaml" },
};

describe("ResearchJobRow request payload", () => {
  it("shows request params (minus manifest_path) in the expanded section", () => {
    render(
      <MemoryRouter>
        <ResearchJobRow job={job} onCancel={() => {}} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByText("sweep"));
    expect(screen.getByText(/search=.?grid/)).toBeInTheDocument();
    expect(screen.getByText(/max_trials=50/)).toBeInTheDocument();
    expect(screen.queryByText(/manifest_path/)).toBeNull();
  });
});
