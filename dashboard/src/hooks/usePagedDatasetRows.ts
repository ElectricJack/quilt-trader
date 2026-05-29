import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { api } from "../api/client";

export function usePagedDatasetRows(
  name: string | null,
  params: {
    symbol?: string;
    start?: string;
    end?: string;
    q?: string;
    page: number;
    pageSize: number;
  }
) {
  return useQuery({
    queryKey: ["datasets", "rows", name, params],
    queryFn: () =>
      api.getDatasetRows(name!, {
        symbol: params.symbol,
        start: params.start,
        end: params.end,
        q: params.q,
        limit: params.pageSize,
        offset: params.page * params.pageSize,
      }),
    enabled: !!name,
    placeholderData: keepPreviousData,
  });
}
