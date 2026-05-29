import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { api } from "../api/client";

export function usePagedDatasetRows(
  name: string | null,
  params: {
    symbol?: string;
    as_of?: string;
    start?: string;
    end?: string;
    page: number;
    pageSize: number;
  }
) {
  return useQuery({
    queryKey: ["datasets", "rows", name, params],
    queryFn: () =>
      api.getDatasetRows(name!, {
        symbol: params.symbol,
        as_of: params.as_of,
        start: params.start,
        end: params.end,
        limit: params.pageSize,
        offset: params.page * params.pageSize,
      }),
    enabled: !!name,
    placeholderData: keepPreviousData,
  });
}
