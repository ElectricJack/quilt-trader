import { createContext, useContext } from "react";

/**
 * Shared context for the Overview page account filter.
 * Widgets read `selectedIds` to determine which accounts to include.
 * An empty Set means "all" is the initial state — but we default to ALL accounts selected,
 * so a non-empty Set is the normal operating state.
 */
export interface OverviewFilterContextValue {
  /** Set of account IDs currently selected (shown). Empty set = nothing selected. */
  selectedIds: Set<string>;
}

export const OverviewFilterContext = createContext<OverviewFilterContextValue>({
  selectedIds: new Set(),
});

/** Consume the overview account filter. Call inside any Overview widget. */
export function useOverviewFilter(): OverviewFilterContextValue {
  return useContext(OverviewFilterContext);
}
