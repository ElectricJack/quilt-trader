import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

export function useDatasetCoverage() {
  return useQuery({
    queryKey: ["datasets", "coverage"],
    queryFn: api.getDatasetCoverageIndex,
  });
}

export function useDatasetCoverageDetail(name: string, enabled = true) {
  return useQuery({
    queryKey: ["datasets", "coverage", name],
    queryFn: () => api.getDatasetCoverage(name),
    enabled,
  });
}
