import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { useResearchSession, useResearchJobs } from "../hooks/useResearchSession";
import {
  useCancelResearchJob,
  useGenerateResearchReport,
} from "../hooks/useResearchMutations";
import { ResearchSessionSummary } from "../components/ResearchSessionSummary";
import { ResearchJobRow } from "../components/ResearchJobRow";
import { NewSweepModal } from "../components/NewSweepModal";

export function ResearchSessionDetail() {
  const { id } = useParams<{ id: string }>();
  const sessionId = id ? parseInt(id, 10) : null;
  const sessionQ = useResearchSession(sessionId);
  const jobsQ = useResearchJobs(sessionId);
  const cancelMut = useCancelResearchJob(sessionId ?? 0);
  const reportMut = useGenerateResearchReport(sessionId ?? 0);
  const [sweepOpen, setSweepOpen] = useState(false);
  const [reportMsg, setReportMsg] = useState<string | null>(null);

  if (sessionQ.isLoading) {
    return <div className="text-gray-400">Loading…</div>;
  }
  if (sessionQ.error || !sessionQ.data) {
    return (
      <div className="bg-gray-900 border border-red-900 rounded-lg p-6 text-red-400">
        Session not found.
        <Link to="/research" className="ml-3 text-indigo-400 hover:text-indigo-300">
          ← back to sessions
        </Link>
      </div>
    );
  }
  const session = sessionQ.data;

  return (
    <div className="space-y-4">
      <Link to="/research" className="text-sm text-gray-400 hover:text-gray-200 flex items-center gap-1">
        <ArrowLeft size={14} /> Back to sessions
      </Link>

      <ResearchSessionSummary
        session={session}
        onNewSweep={() => setSweepOpen(true)}
        reportPending={reportMut.isPending}
        onGenerateReport={async () => {
          setReportMsg(null);
          try {
            const r = await reportMut.mutateAsync();
            setReportMsg(`Report written to ${r.markdown_path} (md) + ${r.html_path} (html)`);
          } catch (e) {
            setReportMsg(`Failed: ${e instanceof Error ? e.message : "unknown"}`);
          }
        }}
      />

      {reportMsg && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-3 text-xs text-gray-300 font-mono">
          {reportMsg}
        </div>
      )}

      <div className="space-y-2">
        <h2 className="text-lg font-semibold text-gray-200">Jobs</h2>
        {jobsQ.data && jobsQ.data.length === 0 && (
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-6 text-center text-gray-400 text-sm">
            No jobs yet. Click <span className="text-white">New Sweep</span> to start one.
          </div>
        )}
        {jobsQ.data?.map((job) => (
          <ResearchJobRow
            key={job.job_id}
            job={job}
            onCancel={(jobId) => void cancelMut.mutate(jobId)}
          />
        ))}
      </div>

      <NewSweepModal
        open={sweepOpen}
        sessionId={sessionId ?? 0}
        onClose={() => setSweepOpen(false)}
      />
    </div>
  );
}
