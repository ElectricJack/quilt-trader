// OCC format: 1-6 letter underlying + 6 digit date (YYMMDD) + C/P + 8 digit strike
const OCC_RE = /^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$/;

export interface ParsedOCC {
  underlying: string;
  expiration: string; // ISO date YYYY-MM-DD
  side: "Call" | "Put";
  strike: number;
}

export function isOCCSymbol(symbol: string): boolean {
  return OCC_RE.test(symbol);
}

export function parseOCC(symbol: string): ParsedOCC | null {
  const m = symbol.match(OCC_RE);
  if (!m) return null;
  const [, underlying, yy, mm, dd, cp, strikeRaw] = m;
  return {
    underlying,
    expiration: `20${yy}-${mm}-${dd}`,
    side: cp === "C" ? "Call" : "Put",
    strike: parseInt(strikeRaw, 10) / 1000,
  };
}

export function formatOCCReadable(symbol: string): string {
  const parsed = parseOCC(symbol);
  if (!parsed) return symbol;
  const { underlying, expiration, side, strike } = parsed;
  const [, mm, dd] = expiration.slice(2).split("-");
  const yy = expiration.slice(2, 4);
  const strikeStr = strike % 1 === 0 ? `$${strike}` : `$${strike.toFixed(2)}`;
  return `${underlying} ${strikeStr} ${side} ${mm}/${dd}/${yy}`;
}
