import React from "react";

interface DashboardGridProps {
  children: React.ReactNode;
}

export function DashboardGrid({ children }: DashboardGridProps) {
  return (
    <div
      style={{ gridTemplateColumns: "repeat(2, 1fr)" }}
      className="grid gap-4"
    >
      {children}
    </div>
  );
}
