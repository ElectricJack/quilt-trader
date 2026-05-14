class MetricsAggregator:
    @staticmethod
    def aggregate_runs(runs: list[dict]) -> dict:
        if not runs:
            return {
                "total_return_dollars": 0, "total_return_pct": 0,
                "total_fees_dollars": 0, "total_trades": 0,
                "winning_trades": 0, "losing_trades": 0,
                "max_drawdown_pct": 0, "net_profit_after_fees": 0,
                "sharpe_ratio": 0, "positions_opened": 0,
                "positions_closed": 0, "positions_open": 0,
            }

        total_pnl = 0
        total_fees = 0
        total_trades = 0
        total_wins = 0
        total_losses = 0
        max_dd = 0
        total_positions_opened = 0
        total_positions_closed = 0
        total_positions_open = 0
        sharpe_values = []

        for run in runs:
            m = run.get("metrics", {})
            if not m:
                continue
            total_pnl += m.get("total_return_dollars", 0)
            total_fees += m.get("total_fees_dollars", 0)
            total_trades += m.get("total_trades", 0)
            total_wins += m.get("winning_trades", 0)
            total_losses += m.get("losing_trades", 0)
            max_dd = max(max_dd, m.get("max_drawdown_pct", 0))
            total_positions_opened += m.get("positions_opened", 0)
            total_positions_closed += m.get("positions_closed", 0)
            total_positions_open += m.get("positions_open", 0)
            if m.get("sharpe_ratio") is not None:
                sharpe_values.append(m["sharpe_ratio"])

        first_equity = runs[0].get("starting_equity", 0)
        total_return_pct = (total_pnl / first_equity * 100) if first_equity else 0
        avg_sharpe = sum(sharpe_values) / len(sharpe_values) if sharpe_values else 0
        win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0

        return {
            "total_return_dollars": round(total_pnl, 2),
            "total_return_pct": round(total_return_pct, 2),
            "total_fees_dollars": round(total_fees, 2),
            "net_profit_after_fees": round(total_pnl - total_fees, 2),
            "total_trades": total_trades,
            "winning_trades": total_wins,
            "losing_trades": total_losses,
            "win_rate_pct": round(win_rate, 1),
            "max_drawdown_pct": round(max_dd, 2),
            "sharpe_ratio": round(avg_sharpe, 2),
            "positions_opened": total_positions_opened,
            "positions_closed": total_positions_closed,
            "positions_open": total_positions_open,
        }
