// dashboard/src/components/strategy/templates.ts
// Pure-function registry of strategy templates. Each template takes a chain
// (spot + contracts) and an expiry and returns a list of OptionLegs.
//
// Templates accept an optional ``anchorStrike`` which re-centers the entire
// structure at that strike instead of spot. This is what powers
// click-to-place: the user clicks a strike in the matrix and the template
// re-anchors there. Width offsets remain proportional to the anchor.

import type { OptionLeg } from "../../lib/options";
import type { OptionChainResponse, OptionChainContract } from "../../api/client";

export type TemplateName =
  | "long_call"
  | "long_put"
  | "short_call"
  | "short_put"
  | "vertical_bull_call"
  | "vertical_bear_call"
  | "vertical_bull_put"
  | "vertical_bear_put"
  | "straddle"
  | "strangle"
  | "iron_condor"
  | "iron_butterfly"
  | "calendar_call"
  | "custom";

export const TEMPLATE_LABELS: { name: TemplateName; label: string }[] = [
  { name: "custom", label: "Custom" },
  { name: "long_call", label: "Long Call" },
  { name: "long_put", label: "Long Put" },
  { name: "short_call", label: "Short Call" },
  { name: "short_put", label: "Short Put" },
  { name: "vertical_bull_call", label: "Bull Call Spread" },
  { name: "vertical_bear_call", label: "Bear Call Spread" },
  { name: "vertical_bull_put", label: "Bull Put Spread" },
  { name: "vertical_bear_put", label: "Bear Put Spread" },
  { name: "straddle", label: "Straddle" },
  { name: "strangle", label: "Strangle" },
  { name: "iron_condor", label: "Iron Condor" },
  { name: "iron_butterfly", label: "Iron Butterfly" },
  { name: "calendar_call", label: "Calendar Spread (Call)" },
];

function pickByStrike(
  chain: OptionChainResponse,
  target: number,
  right: "call" | "put"
): OptionChainContract | null {
  const candidates = chain.contracts.filter((c) => c.right === right);
  if (!candidates.length) return null;
  return candidates.reduce((best, c) =>
    Math.abs(c.strike - target) < Math.abs(best.strike - target) ? c : best
  );
}

function legFrom(
  c: OptionChainContract,
  side: "buy" | "sell",
  expiry: string,
  quantity = 1
): OptionLeg {
  return {
    side,
    right: c.right,
    strike: c.strike,
    expiry,
    quantity,
    bid: c.bid ?? undefined,
    ask: c.ask ?? undefined,
    iv: c.iv ?? 0.3,
  };
}

export function buildTemplate(
  name: TemplateName,
  chain: OptionChainResponse,
  expiry: string,
  anchorStrike?: number
): OptionLeg[] {
  // Use the clicked strike as the structure's reference; fall back to spot.
  const anchor = anchorStrike ?? chain.spot;
  switch (name) {
    case "long_call": {
      const c = pickByStrike(chain, anchor, "call");
      return c ? [legFrom(c, "buy", expiry)] : [];
    }
    case "long_put": {
      const c = pickByStrike(chain, anchor, "put");
      return c ? [legFrom(c, "buy", expiry)] : [];
    }
    case "short_call": {
      const c = pickByStrike(chain, anchor, "call");
      return c ? [legFrom(c, "sell", expiry)] : [];
    }
    case "short_put": {
      const c = pickByStrike(chain, anchor, "put");
      return c ? [legFrom(c, "sell", expiry)] : [];
    }
    case "vertical_bull_call": {
      const lo = pickByStrike(chain, anchor, "call");
      const hi = pickByStrike(chain, anchor * 1.02, "call");
      return lo && hi ? [legFrom(lo, "buy", expiry), legFrom(hi, "sell", expiry)] : [];
    }
    case "vertical_bear_call": {
      const lo = pickByStrike(chain, anchor, "call");
      const hi = pickByStrike(chain, anchor * 1.02, "call");
      return lo && hi ? [legFrom(lo, "sell", expiry), legFrom(hi, "buy", expiry)] : [];
    }
    case "vertical_bull_put": {
      const hi = pickByStrike(chain, anchor, "put");
      const lo = pickByStrike(chain, anchor * 0.98, "put");
      return hi && lo ? [legFrom(hi, "sell", expiry), legFrom(lo, "buy", expiry)] : [];
    }
    case "vertical_bear_put": {
      const hi = pickByStrike(chain, anchor, "put");
      const lo = pickByStrike(chain, anchor * 0.98, "put");
      return hi && lo ? [legFrom(hi, "buy", expiry), legFrom(lo, "sell", expiry)] : [];
    }
    case "straddle": {
      const c = pickByStrike(chain, anchor, "call");
      const p = pickByStrike(chain, anchor, "put");
      return c && p ? [legFrom(c, "buy", expiry), legFrom(p, "buy", expiry)] : [];
    }
    case "strangle": {
      const c = pickByStrike(chain, anchor * 1.03, "call");
      const p = pickByStrike(chain, anchor * 0.97, "put");
      return c && p ? [legFrom(c, "buy", expiry), legFrom(p, "buy", expiry)] : [];
    }
    case "iron_condor": {
      const cs = pickByStrike(chain, anchor * 1.02, "call");
      const cl = pickByStrike(chain, anchor * 1.04, "call");
      const ps = pickByStrike(chain, anchor * 0.98, "put");
      const pl = pickByStrike(chain, anchor * 0.96, "put");
      return cs && cl && ps && pl
        ? [
            legFrom(ps, "sell", expiry),
            legFrom(pl, "buy", expiry),
            legFrom(cs, "sell", expiry),
            legFrom(cl, "buy", expiry),
          ]
        : [];
    }
    case "iron_butterfly": {
      const cs = pickByStrike(chain, anchor, "call");
      const ps = pickByStrike(chain, anchor, "put");
      const cl = pickByStrike(chain, anchor * 1.04, "call");
      const pl = pickByStrike(chain, anchor * 0.96, "put");
      return cs && ps && cl && pl
        ? [
            legFrom(cs, "sell", expiry),
            legFrom(ps, "sell", expiry),
            legFrom(cl, "buy", expiry),
            legFrom(pl, "buy", expiry),
          ]
        : [];
    }
    case "calendar_call": {
      // Same-strike, different expiries — needs a far-expiry UI not in v1.
      return [];
    }
    case "custom":
      return [];
  }
}
