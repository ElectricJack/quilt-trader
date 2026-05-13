import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface WidgetConfig {
  id: string;
  visible: boolean;
  order: number;
  colSpan: 1 | 2;
}

interface DashboardState {
  widgets: WidgetConfig[];
  reorder: (fromIndex: number, toIndex: number) => void;
  toggleWidget: (id: string) => void;
  resetLayout: () => void;
}

const DEFAULT_WIDGETS: WidgetConfig[] = [
  { id: "portfolio-value", visible: true, order: 0, colSpan: 2 },
  { id: "active-algorithms", visible: true, order: 1, colSpan: 1 },
  { id: "todays-pnl", visible: true, order: 2, colSpan: 1 },
  { id: "recent-trades", visible: true, order: 3, colSpan: 1 },
  { id: "open-positions", visible: true, order: 4, colSpan: 1 },
  { id: "worker-health", visible: true, order: 5, colSpan: 1 },
  { id: "backtest-alerts", visible: true, order: 6, colSpan: 1 },
  { id: "system-events", visible: true, order: 7, colSpan: 1 },
  { id: "account-balances", visible: true, order: 8, colSpan: 1 },
];

export const useDashboardStore = create<DashboardState>()(
  persist(
    (set) => ({
      widgets: DEFAULT_WIDGETS,

      reorder: (fromIndex, toIndex) =>
        set((state) => {
          const widgets = [...state.widgets];
          const [moved] = widgets.splice(fromIndex, 1);
          widgets.splice(toIndex, 0, moved);
          return {
            widgets: widgets.map((w, i) => ({ ...w, order: i })),
          };
        }),

      toggleWidget: (id) =>
        set((state) => ({
          widgets: state.widgets.map((w) =>
            w.id === id ? { ...w, visible: !w.visible } : w
          ),
        })),

      resetLayout: () => set({ widgets: DEFAULT_WIDGETS }),
    }),
    {
      name: "quilt-dashboard-layout",
    }
  )
);
