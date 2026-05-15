interface KpiCardProps {
  label: string;
  value: string;
  hint?: string;
  variant?: "hero" | "sub";
}

export function KpiCard({ label, value, hint, variant = "sub" }: KpiCardProps) {
  const valueClass = variant === "hero" ? "text-4xl font-bold text-white" : "text-xl font-semibold text-white";
  const wrapClass = variant === "hero"
    ? "bg-gray-900 border border-gray-800 rounded-lg p-5"
    : "bg-gray-900 border border-gray-800 rounded p-3";
  return (
    <div className={wrapClass}>
      <div className="text-[10px] uppercase tracking-wide text-gray-500" title={hint}>{label}</div>
      <div className={valueClass}>{value}</div>
    </div>
  );
}
