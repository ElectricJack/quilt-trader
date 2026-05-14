import { create } from "zustand";

const MAX_ALERTS = 20;

export interface Alert {
  id: string;
  message: string;
  severity: "info" | "warning" | "error" | "success";
}

interface UIState {
  sidebarOpen: boolean;
  alerts: Alert[];
  toggleSidebar: () => void;
  addAlert: (alert: Omit<Alert, "id">) => void;
  dismissAlert: (id: string) => void;
}

export const useUIStore = create<UIState>((set) => ({
  sidebarOpen: true,
  alerts: [],

  toggleSidebar: () =>
    set((state) => ({ sidebarOpen: !state.sidebarOpen })),

  addAlert: (alert) =>
    set((state) => {
      const newAlert: Alert = {
        ...alert,
        id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
      };
      const alerts = [newAlert, ...state.alerts].slice(0, MAX_ALERTS);
      return { alerts };
    }),

  dismissAlert: (id) =>
    set((state) => ({
      alerts: state.alerts.filter((a) => a.id !== id),
    })),
}));
