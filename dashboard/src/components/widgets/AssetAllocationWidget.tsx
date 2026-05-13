import { useState } from "react";
import { Widget } from "../Widget";
import { Donut } from "../Donut";
import { usePortfolioAllocation } from "../../api/hooks";

function fmtCompact(v: number): string {
  return `$${(v / 1000).toFixed(1)}k`;
}

export function AssetAllocationWidget() {
  const { data, isLoading } = usePortfolioAllocation();
  const [view, setView] = useState<"class" | "symbol">("class");
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
      <div className="flex items-center gap-4 px-4 pb-4">
        <Donut segments={segments} size={130} />
        <div className="flex-1 text-[11px] leading-relaxed">
          {segments.map((s) => (
            <div key={s.key}>
              <span
                className="inline-block w-2.5 h-2.5 rounded-sm mr-2 align-middle"
                style={{ background: s.color }}
              />
              <strong>{s.label}</strong> {s.percent}% · {fmtCompact(s.value_usd)}
            </div>
          ))}
        </div>
      </div>
    </Widget>
  );
}
