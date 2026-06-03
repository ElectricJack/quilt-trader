import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { keys } from "../api/hooks";

export function useResearchSession(id: number | null) {
  return useQuery({
    queryKey: id !== null ? keys.researchSession(id) : ["research", "sessions", "null"],
    queryFn: () => api.getResearchSession(id as number),
    enabled: id !== null,
  });
}

export function useResearchJobs(sessionId: number | null) {
  return useQuery({
    queryKey: sessionId !== null
      ? keys.researchJobs(sessionId)
      : ["research", "sessions", "null", "jobs"],
    queryFn: () => api.listResearchJobs(sessionId as number),
    enabled: sessionId !== null,
  });
}
