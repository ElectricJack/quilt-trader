import { useState, useEffect } from "react";
import { useParams, Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { api } from "../api/client";
import type { ResearchSession, ResearchJob } from "../api/client";
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
  const queryClient = useQueryClient();

  const [session, setSession] = useState<ResearchSession | null>(null);
  const [sessionLoading, setSessionLoading] = useState(true);
  const [sessionError, setSessionError] = useState<Error | null>(null);
  const [jobs, setJobs] = useState<ResearchJob[]>([]);

  useEffect(() => {
    if (sessionId === null) return;
    let cancelled = false;
    setSessionLoading(true);
    setSessionError(null);
    Promise.all([
      api.getResearchSession(sessionId),
      api.listResearchJobs(sessionId),
    ])
      .then(([sess, jobList]) => {
        if (!cancelled) {
          setSession(sess);
          setJobs(jobList);
          setSessionLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setSessionError(err instanceof Error ? err : new Error(String(err)));
          setSessionLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, queryClient]);

  const cancelMut = useCancelResearchJob(sessionId ?? 0);
  const reportMut = useGenerateResearchReport(sessionId ?? 0);
  const [sweepOpen, setSweepOpen] = useState(false);
  const [reportMsg, setReportMsg] = useState<string | null>(null);

  if (sessionLoading) {
    return <div className="text-gray-400">Loading…</div>;
  }
  if (sessionError || !session) {
    return (
      <div className="bg-gray-900 border border-red-900 rounded-lg p-6 text-red-400">
        Session not found.
        <Link to="/research" className="ml-3 text-indigo-400 hover:text-indigo-300">
          ← back to sessions
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <Link
        to="/research"
        className="text-sm text-gray-400 hover:text-gray-200 flex items-center gap-1"
      >
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
        {jobs.length === 0 && (
          <div className="bg-gray-900 border border-gray-800 rounded-lg p-6 text-center text-gray-400 text-sm">
            No jobs yet. Click <span className="text-white">New Sweep</span> to start one.
          </div>
        )}
        {jobs.map((job) => (
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
