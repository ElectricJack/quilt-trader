import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { CreateSessionRequest, CreateSweepRequest } from "../api/client";
import { keys } from "../api/hooks";

export function useCreateResearchSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateSessionRequest) => api.createResearchSession(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.researchSessions() });
    },
  });
}

export function useCreateResearchSweep(sessionId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateSweepRequest) =>
      api.createResearchSweep(sessionId, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.researchJobs(sessionId) });
      void qc.invalidateQueries({ queryKey: keys.researchSession(sessionId) });
    },
  });
}

export function useCancelResearchJob(sessionId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) => api.cancelResearchJob(sessionId, jobId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.researchJobs(sessionId) });
    },
  });
}

export function useGenerateResearchReport(sessionId: number) {
  return useMutation({
    mutationFn: () => api.generateResearchReport(sessionId),
  });
}
