from dataclasses import dataclass, field


@dataclass
class ReconciliationResult:
    matched: list[dict] = field(default_factory=list)
    untracked: list[dict] = field(default_factory=list)
    stale: list[dict] = field(default_factory=list)
    mismatched: list[dict] = field(default_factory=list)


class PositionReconciler:
    @staticmethod
    def reconcile(broker_positions: dict[str, dict], db_positions: list[dict]) -> ReconciliationResult:
        result = ReconciliationResult()

        db_by_symbol: dict[str, list[dict]] = {}
        for dbp in db_positions:
            for leg in dbp.get("legs", []):
                sym = leg.get("symbol")
                if sym:
                    db_by_symbol.setdefault(sym, []).append(dbp)

        seen_db_ids: set[str] = set()

        for sym, broker_pos in broker_positions.items():
            db_matches = db_by_symbol.get(sym, [])
            if not db_matches:
                result.untracked.append(broker_pos)
                continue

            dbp = db_matches[0]
            seen_db_ids.add(dbp["id"])

            db_qty = sum(
                leg.get("quantity", 0)
                for leg in dbp.get("legs", [])
                if leg.get("symbol") == sym
            )
            broker_qty = broker_pos.get("quantity", 0)

            if abs(db_qty - broker_qty) > 1e-9:
                result.mismatched.append({
                    "db_id": dbp["id"],
                    "broker_symbol": sym,
                    "db_qty": db_qty,
                    "broker_qty": broker_qty,
                })
            else:
                result.matched.append({
                    "db_id": dbp["id"],
                    "broker_symbol": sym,
                })

        for dbp in db_positions:
            if dbp["id"] not in seen_db_ids:
                result.stale.append(dbp)

        return result
