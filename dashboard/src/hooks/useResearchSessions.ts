import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { keys } from "../api/hooks";

export function useResearchSessions(filters?: {
  algorithm_id?: string;
  status?: string;
  limit?: number;
}) {
  return useQuery({
    queryKey: keys.researchSessions(filters),
    queryFn: () => api.listResearchSessions(filters),
  });
}
