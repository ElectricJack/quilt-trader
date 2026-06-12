import type { ResearchSession } from "../api/client";

export const SESSION_STATUS_COLORS: Record<ResearchSession["status"], string> = {
  open: "bg-gray-700 text-gray-300",
  running: "bg-blue-700 text-blue-100",
  completed: "bg-green-700 text-green-100",
  failed: "bg-red-700 text-red-100",
};
