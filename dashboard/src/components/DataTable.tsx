import { useState } from "react";
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getPaginationRowModel,
  flexRender,
  SortingState,
  ColumnDef,
} from "@tanstack/react-table";

export type { ColumnDef };

interface DataTableProps<T> {
  data: T[];
  columns: ColumnDef<T, unknown>[];
  isLoading?: boolean;
  emptyMessage?: string;
  onRowClick?: (row: T) => void;
  enableSorting?: boolean;
  enablePagination?: boolean;
  pageSize?: number;
}

export function DataTable<T>({
  data,
  columns,
  isLoading = false,
  emptyMessage = "No data available.",
  onRowClick,
  enableSorting = false,
  enablePagination = false,
  pageSize = 10,
}: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>([]);

  const table = useReactTable<T>({
    data,
    columns,
    state: {
      sorting,
    },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    ...(enableSorting ? { getSortedRowModel: getSortedRowModel() } : {}),
    ...(enablePagination
      ? {
          getPaginationRowModel: getPaginationRowModel(),
          initialState: { pagination: { pageSize } },
        }
      : {}),
  });

  const headerGroups = table.getHeaderGroups();
  const rows = table.getRowModel().rows;
  const colCount = columns.length;

  return (
    <div className="w-full overflow-auto">
      <table className="w-full text-sm text-left">
        <thead>
          {headerGroups.map((headerGroup) => (
            <tr key={headerGroup.id}>
              {headerGroup.headers.map((header) => {
                const canSort = enableSorting && header.column.getCanSort();
                const sortDir = header.column.getIsSorted();
                const sortIndicator = sortDir === "asc" ? "▲" : sortDir === "desc" ? "▼" : canSort ? "↕" : null;

                return (
                  <th
                    key={header.id}
                    className="px-4 py-3 text-xs text-gray-400 uppercase bg-gray-900 border-b border-gray-800 whitespace-nowrap select-none"
                    style={canSort ? { cursor: "pointer" } : undefined}
                    onClick={canSort ? header.column.getToggleSortingHandler() : undefined}
                  >
                    <span className="inline-flex items-center gap-1">
                      {header.isPlaceholder
                        ? null
                        : flexRender(header.column.columnDef.header, header.getContext())}
                      {sortIndicator && (
                        <span className="text-gray-500 text-xs">{sortIndicator}</span>
                      )}
                    </span>
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {isLoading ? (
            Array.from({ length: 3 }).map((_, rowIdx) => (
              <tr key={rowIdx} className="border-b border-gray-800">
                {Array.from({ length: colCount }).map((_, colIdx) => (
                  <td key={colIdx} className="px-4 py-3">
                    <div className="h-4 bg-gray-700 rounded animate-pulse" />
                  </td>
                ))}
              </tr>
            ))
          ) : rows.length === 0 ? (
            <tr>
              <td
                colSpan={colCount}
                className="px-4 py-8 text-center text-gray-500"
              >
                {emptyMessage}
              </td>
            </tr>
          ) : (
            rows.map((row) => (
              <tr
                key={row.id}
                className={[
                  "border-b border-gray-800 text-gray-300 hover:bg-gray-800/50",
                  onRowClick ? "cursor-pointer" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
                onClick={onRowClick ? () => onRowClick(row.original) : undefined}
              >
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="px-4 py-3">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>

      {enablePagination && !isLoading && rows.length > 0 && (
        <div className="flex items-center justify-between px-4 py-3 border-t border-gray-800">
          <span className="text-sm text-gray-400">
            Page {table.getState().pagination.pageIndex + 1} of{" "}
            {table.getPageCount()}
          </span>
          <div className="flex gap-2">
            <button
              className="bg-gray-700 hover:bg-gray-600 text-gray-300 px-3 py-1 rounded text-sm disabled:opacity-50"
              onClick={() => table.previousPage()}
              disabled={!table.getCanPreviousPage()}
            >
              Previous
            </button>
            <button
              className="bg-gray-700 hover:bg-gray-600 text-gray-300 px-3 py-1 rounded text-sm disabled:opacity-50"
              onClick={() => table.nextPage()}
              disabled={!table.getCanNextPage()}
            >
              Next
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
