import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import { WIDGET_REGISTRY } from "../components/widgets";

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
  { id: "portfolio-equity", visible: true, order: 0, colSpan: 2 },
  { id: "kpi-strip", visible: true, order: 1, colSpan: 2 },
  { id: "algorithms", visible: true, order: 2, colSpan: 2 },
  { id: "open-positions", visible: true, order: 3, colSpan: 1 },
  { id: "recent-trades", visible: true, order: 4, colSpan: 1 },
  { id: "account-balances", visible: true, order: 5, colSpan: 1 },
  { id: "asset-allocation", visible: true, order: 6, colSpan: 1 },
  { id: "alerts", visible: true, order: 7, colSpan: 1 },
];

function reconcileLayout(persisted: WidgetConfig[]): WidgetConfig[] {
  const validIds = new Set(Object.keys(WIDGET_REGISTRY));
  const kept = persisted.filter((w) => validIds.has(w.id));
  const persistedIds = new Set(kept.map((w) => w.id));
  const additions = DEFAULT_WIDGETS.filter((w) => !persistedIds.has(w.id)).map(
    (w, i) => ({ ...w, order: kept.length + i })
  );
  return [...kept, ...additions].map((w, i) => ({ ...w, order: i }));
}

export const useDashboardStore = create<DashboardState>()(
  persist(
    (set) => ({
      widgets: DEFAULT_WIDGETS,
      reorder: (fromIndex, toIndex) =>
        set((state) => {
          const widgets = [...state.widgets];
          const [moved] = widgets.splice(fromIndex, 1);
          widgets.splice(toIndex, 0, moved);
          return { widgets: widgets.map((w, i) => ({ ...w, order: i })) };
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
      storage: createJSONStorage(() => localStorage),
      merge: (persisted, current) => {
        if (!persisted || typeof persisted !== "object") return current;
        const ps = persisted as Partial<DashboardState>;
        const widgets = ps.widgets ? reconcileLayout(ps.widgets) : DEFAULT_WIDGETS;
        return { ...current, widgets };
      },
    }
  )
);
