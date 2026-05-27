// dashboard/src/components/strategy/SimpleOrderForm.tsx
// Single-leg order form for equities and crypto. No chain matrix, no
// templates — the strategy builder is only relevant for options.
//
// Atomic multi-leg execution doesn't exist for equities/crypto at the
// broker layer (Alpaca/Tradier submit each leg as a separate ticket),
// so we deliberately constrain this form to a single leg to avoid
// partial-fill exposure.
import { useState } from "react";
import type { useOpenPosition } from "../../api/hooks";

type AssetType = "equities" | "crypto";

interface Props {
  assetType: AssetType;
  submitMut: ReturnType<typeof useOpenPosition>;
  onAfterSubmit?: () => void;
}

export function SimpleOrderForm({ assetType, submitMut, onAfterSubmit }: Props) {
  const [symbol, setSymbol] = useState("");
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [quantity, setQuantity] = useState<number>(1);
  const [orderType, setOrderType] = useState<"market" | "limit">("market");
  const [limitPrice, setLimitPrice] = useState<number | null>(null);

  const canSubmit = !!symbol.trim() && quantity > 0 && !submitMut.isPending &&
    (orderType === "market" || (orderType === "limit" && limitPrice != null && limitPrice > 0));

  async function handleSubmit() {
    if (!canSubmit) return;
    try {
      await submitMut.mutateAsync({
        legs: [{
          symbol: symbol.trim().toUpperCase(),
          asset_type: assetType,
          side,
          quantity,
        }],
        order_type: orderType,
        ...(orderType === "limit" && limitPrice != null ? { limit_price: limitPrice } : {}),
      });
      setSymbol("");
      setQuantity(1);
      setLimitPrice(null);
      onAfterSubmit?.();
    } catch {
      // Caller surfaces error via mutation onError / addAlert in parent
    }
  }

  const placeholder = assetType === "crypto" ? "e.g. BTCUSD" : "e.g. AAPL";
  const qtyStep = assetType === "crypto" ? 0.0001 : 1;

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900">
      <div className="px-3 py-2 border-b border-gray-800">
        <h3 className="text-sm font-semibold text-gray-200">
          New {assetType === "crypto" ? "Crypto" : "Equity"} Order
        </h3>
      </div>
      <div className="p-4 space-y-3 max-w-xl">
        <div className="flex gap-3 flex-wrap items-end">
          <label className="text-xs text-gray-400 flex flex-col gap-1">
            Symbol
            <input
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              placeholder={placeholder}
              className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm w-36 text-gray-100"
            />
          </label>
          <label className="text-xs text-gray-400 flex flex-col gap-1">
            Side
            <select
              value={side}
              onChange={(e) => setSide(e.target.value as "buy" | "sell")}
              className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-100"
            >
              <option value="buy">Buy</option>
              <option value="sell">Sell</option>
            </select>
          </label>
          <label className="text-xs text-gray-400 flex flex-col gap-1">
            Quantity
            <input
              type="number"
              min={qtyStep}
              step={qtyStep}
              value={quantity}
              onChange={(e) => setQuantity(Math.max(0, Number(e.target.value) || 0))}
              className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm w-28 text-gray-100"
            />
          </label>
        </div>
        <div className="flex gap-3 flex-wrap items-end">
          <label className="text-xs text-gray-400 flex flex-col gap-1">
            Order type
            <select
              value={orderType}
              onChange={(e) => setOrderType(e.target.value as "market" | "limit")}
              className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-100"
            >
              <option value="market">Market</option>
              <option value="limit">Limit</option>
            </select>
          </label>
          {orderType === "limit" && (
            <label className="text-xs text-gray-400 flex flex-col gap-1">
              Limit price
              <input
                type="number"
                value={limitPrice ?? ""}
                step={0.01}
                onChange={(e) => setLimitPrice(e.target.value ? Number(e.target.value) : null)}
                className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm w-32 text-gray-100"
              />
            </label>
          )}
        </div>
        <div className="pt-2">
          <button
            onClick={handleSubmit}
            disabled={!canSubmit}
            className="px-4 py-2 rounded text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {submitMut.isPending ? "Submitting…" : "Submit order"}
          </button>
        </div>
      </div>
    </div>
  );
}
