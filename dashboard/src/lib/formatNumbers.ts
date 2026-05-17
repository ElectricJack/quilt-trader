export function fmtPct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(2)}%`;
}

export function fmtInt(v: number | null | undefined): string {
  if (v == null) return "—";
  return Math.round(v).toString();
}

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null) return "—";
  return v.toLocaleString("en-US", { maximumFractionDigits: digits });
}
