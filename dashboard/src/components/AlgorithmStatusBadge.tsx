type Status = "live" | "paper" | "idle";

const STYLES: Record<Status, { bg: string; text: string; label: string }> = {
  live:  { bg: "bg-emerald-500/15", text: "text-emerald-400", label: "● live" },
  paper: { bg: "bg-amber-500/15",   text: "text-amber-400",   label: "● paper" },
  idle:  { bg: "bg-gray-500/15",    text: "text-gray-400",    label: "● idle" },
};

export function AlgorithmStatusBadge({ status }: { status: Status }) {
  const s = STYLES[status] ?? STYLES.idle;
  return (
    <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${s.bg} ${s.text}`}>
      {s.label}
    </span>
  );
}
