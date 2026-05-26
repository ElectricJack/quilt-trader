import { useState } from "react";
import { Widget } from "../Widget";
import { Donut } from "../Donut";
import { usePortfolioAllocation } from "../../api/hooks";

function fmtCompact(v: number): string {
  return `$${(v / 1000).toFixed(1)}k`;
}

export function AssetAllocationWidget() {
  const { data, isLoading } = usePortfolioAllocation();
  const [view, setView] = useState<"class" | "symbol">("symbol");
  const segments = (view === "class" ? data?.by_class : data?.by_symbol) ?? [];

  return (
    <Widget title="Asset Allocation" isLoading={isLoading} bodyClass="">
      <div className="flex gap-1 px-3.5 pt-2 pb-2">
        {(["class", "symbol"] as const).map((v) => (
          <button
            key={v}
            onClick={() => setView(v)}
            className={`px-2.5 py-0.5 rounded text-[10px] capitalize ${
              v === view ? "bg-indigo-500 text-white" : "bg-gray-800 text-gray-400"
            }`}
          >
            By {v === "class" ? "Class" : "Symbol"}
          </button>
        ))}
      </div>
      <div className="flex items-center gap-6 px-4 pb-4">
        <div className="shrink-0">
          <Donut segments={segments} size={180} />
        </div>
        <div className="flex-1 text-[11px] leading-loose">
          {segments.map((s) => (
            <div key={s.key} className="flex items-center gap-2">
              <span
                className="inline-block w-2.5 h-2.5 rounded-sm shrink-0"
                style={{ background: s.color }}
              />
              <span className="flex-1">
                <strong>{s.label}</strong>{" "}
                <span className="text-gray-400">{s.percent}% · {fmtCompact(s.value_usd)}</span>
              </span>
            </div>
          ))}
        </div>
      </div>
    </Widget>
  );
}
