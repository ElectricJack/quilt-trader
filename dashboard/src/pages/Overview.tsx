import { useState, useEffect, useCallback, useMemo } from "react";
import { Settings2, X, RotateCcw } from "lucide-react";
import { DashboardGrid } from "../components/DashboardGrid";
import { useDashboardStore } from "../stores/dashboard";
import { WIDGET_REGISTRY, WIDGET_TITLES } from "../components/widgets";
import { useAccounts } from "../api/hooks";
import { OverviewFilterContext } from "../stores/overviewFilter";

function getGreeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

interface CustomizeModalProps {
  onClose: () => void;
}

function CustomizeModal({ onClose }: CustomizeModalProps) {
  const { widgets, toggleWidget, resetLayout } = useDashboardStore();

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/60"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Modal */}
      <div className="relative z-10 bg-gray-900 border border-gray-700 rounded-lg shadow-xl p-6 w-full max-w-sm mx-4">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-semibold text-white">
            Customize Dashboard
          </h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-200 transition-colors"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <ul className="space-y-2 mb-6">
          {widgets.map((w) => (
            <li key={w.id} className="flex items-center gap-3">
              <input
                id={`widget-toggle-${w.id}`}
                type="checkbox"
                checked={w.visible}
                onChange={() => toggleWidget(w.id)}
                className="w-4 h-4 rounded border-gray-600 bg-gray-800 text-blue-500 focus:ring-blue-500 focus:ring-offset-gray-900 cursor-pointer"
              />
              <label
                htmlFor={`widget-toggle-${w.id}`}
                className="text-sm text-gray-200 cursor-pointer select-none"
              >
                {WIDGET_TITLES[w.id] ?? w.id}
              </label>
            </li>
          ))}
        </ul>

        <div className="flex items-center justify-between">
          <button
            onClick={() => resetLayout()}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium text-gray-300 bg-gray-700 hover:bg-gray-600 transition-colors"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            Reset Layout
          </button>
          <button
            onClick={onClose}
            className="px-4 py-1.5 rounded text-sm font-medium text-white bg-blue-600 hover:bg-blue-500 transition-colors"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Account filter ────────────────────────────────────────────────────────────

const STORAGE_KEY = "overview-selected-accounts";

function useOverviewAccountFilter(accountIds: string[]) {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      try {
        const parsed = JSON.parse(saved) as unknown;
        if (Array.isArray(parsed)) {
          return new Set(parsed as string[]);
        }
      } catch {
        // ignore bad JSON
      }
    }
    return new Set(accountIds);
  });

  // When a new account appears, include it by default.
  useEffect(() => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      let changed = false;
      for (const id of accountIds) {
        if (!next.has(id)) {
          next.add(id);
          changed = true;
        }
      }
      if (changed) {
        localStorage.setItem(STORAGE_KEY, JSON.stringify([...next]));
        return next;
      }
      return prev;
    });
  }, [accountIds]);

  const toggleAccount = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      localStorage.setItem(STORAGE_KEY, JSON.stringify([...next]));
      return next;
    });
  }, []);

  const selectAll = useCallback(() => {
    const next = new Set(accountIds);
    localStorage.setItem(STORAGE_KEY, JSON.stringify([...next]));
    setSelectedIds(next);
  }, [accountIds]);

  const selectNone = useCallback(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([]));
    setSelectedIds(new Set());
  }, []);

  const allSelected = accountIds.length > 0 && accountIds.every((id) => selectedIds.has(id));
  const noneSelected = selectedIds.size === 0;

  return { selectedIds, toggleAccount, selectAll, selectNone, allSelected, noneSelected };
}

// ─── Overview page ─────────────────────────────────────────────────────────────

export function Overview() {
  const { widgets, reorder } = useDashboardStore();
  const [showCustomize, setShowCustomize] = useState(false);
  const [dragIdx, setDragIdx] = useState<number | null>(null);

  const { data: accountsData } = useAccounts();
  // Stable references — only change when data actually changes.
  const accounts = useMemo(() => accountsData ?? [], [accountsData]);
  const accountIds = useMemo(() => accounts.map((a) => a.id), [accounts]);

  const { selectedIds, toggleAccount, selectAll, selectNone, noneSelected, allSelected } =
    useOverviewAccountFilter(accountIds);

  const visibleWidgets = widgets
    .filter((w) => w.visible)
    .sort((a, b) => a.order - b.order);

  return (
    <OverviewFilterContext.Provider value={{ selectedIds }}>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold text-white">{getGreeting()}</h1>
          <button
            onClick={() => setShowCustomize(true)}
            className="inline-flex items-center gap-2 px-3 py-1.5 rounded text-sm font-medium text-gray-300 bg-gray-800 border border-gray-700 hover:bg-gray-700 transition-colors"
          >
            <Settings2 className="w-4 h-4" />
            Customize
          </button>
        </div>

        {/* Account filter bar */}
        {accounts.length > 0 && (
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-gray-500 uppercase tracking-wide">Accounts:</span>
            {accounts.map((a) => (
              <button
                key={a.id}
                onClick={() => toggleAccount(a.id)}
                className={`px-2 py-1 rounded text-xs font-medium transition-colors ${
                  selectedIds.has(a.id)
                    ? "bg-indigo-600 text-white"
                    : "bg-gray-800 text-gray-400 hover:text-white"
                }`}
              >
                {a.name}
              </button>
            ))}
            {!allSelected && (
              <button
                onClick={selectAll}
                className="px-2 py-1 rounded text-xs text-gray-500 hover:text-gray-300 transition-colors"
              >
                Select all
              </button>
            )}
            {!noneSelected && accounts.length > 1 && (
              <button
                onClick={selectNone}
                className="px-2 py-1 rounded text-xs text-gray-500 hover:text-gray-300 transition-colors"
              >
                Clear
              </button>
            )}
          </div>
        )}

        {/* Widget grid */}
        <DashboardGrid>
          {visibleWidgets.map((config, idx) => {
            const WidgetComponent = WIDGET_REGISTRY[config.id];
            if (!WidgetComponent) return null;
            return (
              <div
                key={config.id}
                style={{ gridColumn: `span ${config.colSpan}` }}
                draggable
                onDragStart={() => setDragIdx(idx)}
                onDragOver={(e) => e.preventDefault()}
                onDrop={() => {
                  if (dragIdx !== null) reorder(dragIdx, idx);
                  setDragIdx(null);
                }}
              >
                <WidgetComponent />
              </div>
            );
          })}
        </DashboardGrid>

        {/* Customize modal */}
        {showCustomize && (
          <CustomizeModal onClose={() => setShowCustomize(false)} />
        )}
      </div>
    </OverviewFilterContext.Provider>
  );
}
