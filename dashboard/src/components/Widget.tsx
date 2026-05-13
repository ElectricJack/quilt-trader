import React from "react";
import { GripVertical } from "lucide-react";

interface WidgetProps {
  title: string;
  children: React.ReactNode;
  isLoading?: boolean;
  colSpan?: 1 | 2;
  onDragStart?: (e: React.DragEvent) => void;
  onDragOver?: (e: React.DragEvent) => void;
  onDrop?: (e: React.DragEvent) => void;
  className?: string;
}

function SkeletonBars() {
  return (
    <div className="space-y-3 animate-pulse">
      <div className="h-4 bg-gray-800 rounded w-3/4" />
      <div className="h-4 bg-gray-800 rounded w-full" />
      <div className="h-4 bg-gray-800 rounded w-5/6" />
      <div className="h-4 bg-gray-800 rounded w-2/3" />
    </div>
  );
}

export function Widget({
  title,
  children,
  isLoading = false,
  colSpan = 1,
  onDragStart,
  onDragOver,
  onDrop,
  className = "",
}: WidgetProps) {
  return (
    <div
      draggable
      onDragStart={onDragStart}
      onDragOver={onDragOver}
      onDrop={onDrop}
      style={{ gridColumn: `span ${colSpan}` }}
      className={`bg-gray-900 border border-gray-800 rounded-lg overflow-hidden ${className}`}
    >
      <div className="flex items-center gap-2 px-4 py-3 border-b border-gray-800">
        <GripVertical className="text-gray-600 cursor-grab w-4 h-4 shrink-0" />
        <span className="text-sm font-medium text-gray-200">{title}</span>
      </div>
      <div className="p-4">
        {isLoading ? <SkeletonBars /> : children}
      </div>
    </div>
  );
}
