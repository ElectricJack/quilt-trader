import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { useOpenPosition } from "../api/hooks";
import { useUIStore } from "../stores/ui";

type Leg = {
  asset_type: "equities" | "options" | "crypto";
  symbol: string;
  side: "buy" | "sell";
  quantity: number;
  expiry?: string;
  strike?: number;
  right?: "call" | "put";
};

interface Props {
  open: boolean;
  onClose: () => void;
  accountId: string;
  allowedAssetTypes: string[];
}

export function OpenPositionModal({ open, onClose, accountId, allowedAssetTypes }: Props) {
  const openMut = useOpenPosition(accountId);
  const addAlert = useUIStore((s) => s.addAlert);
  const [legs, setLegs] = useState<Leg[]>([{
    asset_type: (allowedAssetTypes[0] ?? "equities") as Leg["asset_type"],
    symbol: "",
    side: "buy",
    quantity: 1,
  }]);
  const [orderType, setOrderType] = useState<"market" | "limit">("market");
  const [limitPrice, setLimitPrice] = useState<number | null>(null);

  if (!open) return null;

  function updateLeg(i: number, patch: Partial<Leg>) {
    setLegs((cur) => cur.map((l, idx) => idx === i ? { ...l, ...patch } : l));
  }

  function addLeg() {
    setLegs((cur) => [...cur, {
      asset_type: (allowedAssetTypes[0] ?? "equities") as Leg["asset_type"],
      symbol: "",
      side: "buy",
      quantity: 1,
    }]);
  }

  function removeLeg(i: number) {
    setLegs((cur) => cur.filter((_, idx) => idx !== i));
  }

  async function submit() {
    try {
      const result = await openMut.mutateAsync({
        legs: legs.map((l) => ({
          symbol: l.symbol,
          asset_type: l.asset_type,
          side: l.side,
          quantity: l.quantity,
          ...(l.asset_type === "options" ? {
            expiry: l.expiry,
            strike: l.strike,
            right: l.right,
          } : {}),
        })),
        order_type: orderType,
        ...(orderType === "limit" && limitPrice != null ? { limit_price: limitPrice } : {}),
      });
      const filledCount = result.legs.filter((l) => l.status === "filled").length;
      addAlert({
        message: result.partial_fill
          ? `Partial fill: ${filledCount}/${result.legs.length} legs filled`
          : `Position opened (${filledCount} legs)`,
        severity: result.partial_fill ? "warning" : "success",
      });
      onClose();
    } catch (e) {
      addAlert({ message: `Failed: ${(e as Error).message}`, severity: "error" });
    }
  }

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
      <div className="bg-gray-900 border border-gray-700 rounded-lg p-5 w-full max-w-3xl">
        <h2 className="text-lg font-semibold mb-3">Open Position</h2>
        <div className="space-y-2 mb-3">
          {legs.map((l, i) => (
            <div key={i} className="flex gap-2 items-end flex-wrap">
              <select
                value={l.asset_type}
                onChange={(e) => updateLeg(i, { asset_type: e.target.value as Leg["asset_type"] })}
                className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm"
              >
                {allowedAssetTypes.map((t) => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
              <input
                value={l.symbol}
                placeholder="symbol"
                onChange={(e) => updateLeg(i, { symbol: e.target.value.toUpperCase() })}
                className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm w-24"
              />
              <select
                value={l.side}
                onChange={(e) => updateLeg(i, { side: e.target.value as "buy" | "sell" })}
                className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm"
              >
                <option value="buy">Buy</option>
                <option value="sell">Sell</option>
              </select>
              <input
                type="number"
                value={l.quantity}
                min={0.0001}
                step={0.0001}
                onChange={(e) => updateLeg(i, { quantity: Number(e.target.value) })}
                className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm w-20"
              />
              {l.asset_type === "options" && (
                <>
                  <input
                    type="date"
                    value={l.expiry ?? ""}
                    onChange={(e) => updateLeg(i, { expiry: e.target.value })}
                    className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm"
                  />
                  <input
                    type="number"
                    placeholder="strike"
                    value={l.strike ?? ""}
                    onChange={(e) => updateLeg(i, { strike: Number(e.target.value) })}
                    className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm w-24"
                  />
                  <select
                    value={l.right ?? "call"}
                    onChange={(e) => updateLeg(i, { right: e.target.value as "call" | "put" })}
                    className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm"
                  >
                    <option value="call">Call</option>
                    <option value="put">Put</option>
                  </select>
                </>
              )}
              <button
                onClick={() => removeLeg(i)}
                className="p-1 text-gray-400 hover:text-red-400"
                aria-label={`remove leg ${i}`}
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>
        <button
          onClick={addLeg}
          className="flex items-center gap-1 text-sm text-indigo-400 hover:text-indigo-300 mb-3"
        >
          <Plus size={14} /> Add leg
        </button>
        <div className="flex gap-3 items-center mb-3">
          <label className="text-sm">Order type:</label>
          <select
            value={orderType}
            onChange={(e) => setOrderType(e.target.value as "market" | "limit")}
            className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm"
          >
            <option value="market">Market</option>
            <option value="limit">Limit</option>
          </select>
          {orderType === "limit" && (
            <input
              type="number"
              placeholder="limit price"
              value={limitPrice ?? ""}
              onChange={(e) => setLimitPrice(e.target.value ? Number(e.target.value) : null)}
              className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm w-32"
            />
          )}
        </div>
        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-3 py-1.5 rounded text-sm text-gray-300 bg-gray-700 hover:bg-gray-600"
          >
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={openMut.isPending || legs.some((l) => !l.symbol)}
            className="px-3 py-1.5 rounded text-sm text-white bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50"
          >
            {openMut.isPending ? "Submitting…" : "Submit"}
          </button>
        </div>
      </div>
    </div>
  );
}
