import { useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { ResearchJob } from "../api/client";

interface Props {
  job: ResearchJob;
  onCancel: (jobId: string) => void;
}

const STATUS_COLORS: Record<ResearchJob["status"], string> = {
  queued:    "bg-gray-700 text-gray-300",
  running:   "bg-blue-700 text-blue-100",
  completed: "bg-green-700 text-green-100",
  failed:    "bg-red-700 text-red-100",
  cancelled: "bg-yellow-700 text-yellow-100",
};

const KIND_COLORS: Record<ResearchJob["kind"], string> = {
  sweep:           "bg-indigo-700 text-indigo-100",
  "walk-forward":  "bg-purple-700 text-purple-100",
};

export function ResearchJobRow({ job, onCancel }: Props) {
  const [expanded, setExpanded] = useState(false);
  const canCancel = job.status === "queued" || job.status === "running";
  const pct = Math.round(job.progress_pct * 100);

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
      <div
        className="flex items-center gap-3 px-4 py-3 cursor-pointer hover:bg-gray-800/50"
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded ? <ChevronDown size={14} className="text-gray-500" /> : <ChevronRight size={14} className="text-gray-500" />}

        <span className={`text-xs font-medium px-2 py-0.5 rounded ${KIND_COLORS[job.kind]}`}>
          {job.kind}
        </span>
        <span className={`text-xs font-medium px-2 py-0.5 rounded ${STATUS_COLORS[job.status]}`}>
          {job.status}
        </span>

        <div className="flex-1 min-w-0">
          <div className="h-1.5 bg-gray-800 rounded-full overflow-hidden">
            <div
              data-testid="progress-fill"
              className="h-full bg-indigo-500"
              style={{ width: `${pct}%` }}
            />
          </div>
          <div className="text-xs text-gray-500 mt-1 truncate">
            {job.progress_message ?? "—"}
          </div>
        </div>

        <div
          className="text-xs text-gray-400 whitespace-nowrap"
          onClick={(e) => { e.stopPropagation(); setExpanded((v) => !v); }}
        >
          {job.run_ids.length > 0 ? `${job.run_ids.length} runs` : "—"}
        </div>

        {canCancel && (
          <button
            onClick={(e) => { e.stopPropagation(); onCancel(job.job_id); }}
            className="text-xs text-red-400 hover:text-red-300 px-2 py-1 border border-red-900 rounded"
          >
            Cancel
          </button>
        )}
      </div>

      {expanded && (
        <div className="bg-gray-950 border-t border-gray-800 px-4 py-3 text-xs space-y-2">
          <div>
            <span className="text-gray-500">job_id:</span>{" "}
            <code className="text-gray-300">{job.job_id}</code>
          </div>
          {job.started_at && (
            <div>
              <span className="text-gray-500">started:</span>{" "}
              <span className="text-gray-300">{job.started_at}</span>
            </div>
          )}
          {job.completed_at && (
            <div>
              <span className="text-gray-500">completed:</span>{" "}
              <span className="text-gray-300">{job.completed_at}</span>
            </div>
          )}
          {job.error_message && (
            <div className="text-red-400">
              <span className="text-gray-500">error:</span> {job.error_message}
            </div>
          )}
          {job.run_ids.length > 0 && (
            <div>
              <div className="text-gray-500 mb-1">runs:</div>
              <div className="flex flex-wrap gap-1">
                {job.run_ids.map((rid) => (
                  <Link
                    key={rid}
                    to={`/backtests/runs/${rid}`}
                    className="text-indigo-400 hover:text-indigo-300 text-xs px-2 py-0.5 border border-indigo-900 rounded"
                  >
                    {rid}
                  </Link>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
