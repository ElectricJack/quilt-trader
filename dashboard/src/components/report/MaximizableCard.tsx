import { useEffect, useState, type ReactNode } from "react";
import { Maximize2, Minimize2 } from "lucide-react";

interface Props {
  title: ReactNode;
  /** Slot-specific controls rendered in the header next to the maximize button. */
  toolbar?: ReactNode;
  /** When the children need to react to maximize state (e.g. resize a chart),
   *  pass a render function instead of a node. */
  children: ReactNode | ((isMax: boolean) => ReactNode);
}

export function MaximizableCard({ title, toolbar, children }: Props) {
  const [isMax, setMax] = useState(false);

  // Esc closes the overlay; lock body scroll while maximized.
  useEffect(() => {
    if (!isMax) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMax(false);
    };
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener("keydown", onKey);
    };
  }, [isMax]);

  const cardClass = isMax
    ? "fixed inset-4 z-50 bg-gray-900 border border-gray-700 rounded shadow-2xl p-4 flex flex-col"
    : "bg-gray-900 border border-gray-800 rounded p-3 flex flex-col h-full min-h-[280px]";

  return (
    <>
      {isMax && (
        <div
          className="fixed inset-0 z-40 bg-black/70"
          onClick={() => setMax(false)}
        />
      )}
      <div className={cardClass}>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold text-gray-300">{title}</h3>
          <div className="flex items-center gap-2">
            {toolbar}
            <button
              onClick={() => setMax(!isMax)}
              className="p-1 rounded text-gray-400 hover:text-gray-100 hover:bg-gray-800"
              title={isMax ? "Restore (Esc)" : "Maximize"}
              aria-label={isMax ? "Restore card" : "Maximize card"}
            >
              {isMax ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
            </button>
          </div>
        </div>
        <div className="flex-1 min-h-0">
          {typeof children === "function" ? children(isMax) : children}
        </div>
      </div>
    </>
  );
}
