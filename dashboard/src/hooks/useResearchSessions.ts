import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { keys } from "../api/hooks";

export function useResearchSessions() {
  return useQuery({
    queryKey: keys.researchSessions(),
    queryFn: api.listResearchSessions,
  });
}
