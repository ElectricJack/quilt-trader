import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { ResearchSession } from "../api/client";
import { JsonTextField } from "./JsonTextField";

interface Props {
  session: ResearchSession;
  onNewSweep: () => void;
  onGenerateReport: () => void;
  reportPending: boolean;
}

const STATUS_COLORS: Record<ResearchSession["status"], string> = {
  open:      "bg-gray-700 text-gray-300",
  running:   "bg-blue-700 text-blue-100",
  completed: "bg-green-700 text-green-100",
  failed:    "bg-red-700 text-red-100",
};

export function ResearchSessionSummary({
  session,
  onNewSweep,
  onGenerateReport,
  reportPending,
}: Props) {
  const [hypExpanded, setHypExpanded] = useState(false);
  const canReport = session.n_runs > 0;

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-5 space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-white truncate">{session.name}</h1>
            <span className={`text-xs font-medium px-2 py-0.5 rounded ${STATUS_COLORS[session.status]}`}>
              {session.status}
            </span>
          </div>
          <div className="text-xs text-gray-500 mt-1">
            Created {session.created_at} · {session.n_runs} run{session.n_runs === 1 ? "" : "s"}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={onGenerateReport}
            disabled={!canReport || reportPending}
            className="bg-gray-800 hover:bg-gray-700 text-gray-200 text-sm px-3 py-1.5 rounded border border-gray-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {reportPending ? "Generating…" : "Generate Report"}
          </button>
          <button
            onClick={onNewSweep}
            className="bg-indigo-600 hover:bg-indigo-500 text-white text-sm px-4 py-1.5 rounded"
          >
            New Sweep
          </button>
        </div>
      </div>

      <div>
        <button
          onClick={() => setHypExpanded((v) => !v)}
          className="flex items-center gap-1 text-sm text-gray-400 hover:text-gray-200"
        >
          {hypExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          Hypothesis
        </button>
        {hypExpanded && (
          <div className="mt-2 text-sm text-gray-300 whitespace-pre-wrap bg-gray-950 border border-gray-800 rounded p-3">
            {session.hypothesis}
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 gap-4">
        <JsonTextField
          label="Parameter space"
          value={session.parameter_space as Record<string, unknown>}
          onChange={() => {}}
          disabled
          rows={5}
        />
        <JsonTextField
          label="Pre-registered criteria"
          value={session.pre_registered_criteria as Record<string, unknown>}
          onChange={() => {}}
          disabled
          rows={5}
        />
      </div>

      {session.notes && (
        <div>
          <div className="text-xs text-gray-500 mb-1">Notes</div>
          <div className="text-sm text-gray-300 whitespace-pre-wrap bg-gray-950 border border-gray-800 rounded p-3">
            {session.notes}
          </div>
        </div>
      )}
    </div>
  );
}
