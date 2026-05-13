import math
from datetime import datetime
from typing import Optional


class MetricsEngine:
    @staticmethod
    def compute(
        equity_curve: list[dict],
        positions: list[dict],
        risk_free_rate: float = 0.0,
    ) -> dict:
        if len(equity_curve) < 2:
            return MetricsEngine._empty_metrics(len(positions))

        equities = [p["equity"] for p in equity_curve]
        start_equity = equities[0]
        end_equity = equities[-1]

        total_return_dollars = end_equity - start_equity
        total_return_pct = (total_return_dollars / start_equity) * 100 if start_equity else 0

        # Daily returns
        returns = []
        for i in range(1, len(equities)):
            if equities[i - 1] != 0:
                returns.append((equities[i] - equities[i - 1]) / equities[i - 1])

        # Duration in days
        try:
            start_dt = datetime.fromisoformat(equity_curve[0]["timestamp"].replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(equity_curve[-1]["timestamp"].replace("Z", "+00:00"))
            duration_days = max((end_dt - start_dt).total_seconds() / 86400, 1)
        except Exception:
            duration_days = len(equity_curve)

        # Annualized return
        if duration_days > 0 and start_equity > 0:
            annualized = ((end_equity / start_equity) ** (365 / duration_days) - 1) * 100
        else:
            annualized = 0.0

        # Volatility + Sharpe
        if len(returns) > 1:
            mean_return = sum(returns) / len(returns)
            variance = sum((r - mean_return) ** 2 for r in returns) / (len(returns) - 1)
            daily_vol = math.sqrt(variance)
            annual_vol = daily_vol * math.sqrt(252)
            sharpe = (mean_return * 252 - risk_free_rate) / (annual_vol) if annual_vol > 0 else 0
        else:
            mean_return = 0
            daily_vol = 0
            annual_vol = 0
            sharpe = 0

        # Sortino (downside deviation)
        downside = [r for r in returns if r < 0]
        if len(downside) > 1:
            downside_var = sum(r ** 2 for r in downside) / len(downside)
            downside_dev = math.sqrt(downside_var) * math.sqrt(252)
            sortino = (mean_return * 252 - risk_free_rate) / downside_dev if downside_dev > 0 else 0
        else:
            sortino = 0

        # Max drawdown
        peak = equities[0]
        max_dd_pct = 0
        max_dd_dollars = 0
        max_dd_duration = 0
        current_dd_start = 0

        for i, eq in enumerate(equities):
            if eq > peak:
                if current_dd_start > 0:
                    dd_dur = i - current_dd_start
                    max_dd_duration = max(max_dd_duration, dd_dur)
                peak = eq
                current_dd_start = 0
            else:
                dd = (peak - eq) / peak * 100 if peak > 0 else 0
                dd_abs = peak - eq
                if dd > max_dd_pct:
                    max_dd_pct = dd
                    max_dd_dollars = dd_abs
                if current_dd_start == 0:
                    current_dd_start = i

        calmar = annualized / max_dd_pct if max_dd_pct > 0 else 0

        # Trade stats
        wins = [p for p in positions if p.get("net_pnl", 0) > 0]
        losses = [p for p in positions if p.get("net_pnl", 0) <= 0]
        total_trades = len(positions)
        winning_trades = len(wins)
        losing_trades = len(losses)
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0
        loss_rate = 100 - win_rate if total_trades > 0 else 0

        total_win = sum(p["net_pnl"] for p in wins) if wins else 0
        total_loss = abs(sum(p["net_pnl"] for p in losses)) if losses else 0
        profit_factor = total_win / total_loss if total_loss > 0 else 0

        avg_win = total_win / winning_trades if winning_trades > 0 else 0
        avg_loss = sum(p["net_pnl"] for p in losses) / losing_trades if losing_trades > 0 else 0
        largest_win = max((p["net_pnl"] for p in wins), default=0)
        largest_loss = min((p["net_pnl"] for p in losses), default=0)

        total_fees = sum(p.get("total_fees", 0) for p in positions)
        total_slippage = sum(p.get("total_slippage", 0) for p in positions)
        net_pnl = sum(p.get("net_pnl", 0) for p in positions)

        # Consecutive wins/losses
        max_consec_wins = 0
        max_consec_losses = 0
        current_wins = 0
        current_losses = 0
        for p in positions:
            if p.get("net_pnl", 0) > 0:
                current_wins += 1
                current_losses = 0
                max_consec_wins = max(max_consec_wins, current_wins)
            else:
                current_losses += 1
                current_wins = 0
                max_consec_losses = max(max_consec_losses, current_losses)

        expectancy = net_pnl / total_trades if total_trades > 0 else 0
        expectancy_pct = (expectancy / start_equity * 100) if start_equity > 0 else 0

        open_positions = sum(1 for p in positions if p.get("closed_at") is None)
        closed_positions = total_trades - open_positions

        return {
            "total_return_pct": round(total_return_pct, 2),
            "total_return_dollars": round(total_return_dollars, 2),
            "annualized_return_pct": round(annualized, 2),
            "time_weighted_return_pct": round(total_return_pct, 2),
            "sharpe_ratio": round(sharpe, 2),
            "sortino_ratio": round(sortino, 2),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "max_drawdown_dollars": round(max_dd_dollars, 2),
            "max_drawdown_duration_days": max_dd_duration,
            "calmar_ratio": round(calmar, 2),
            "win_rate_pct": round(win_rate, 1),
            "loss_rate_pct": round(loss_rate, 1),
            "profit_factor": round(profit_factor, 2),
            "avg_win_dollars": round(avg_win, 2),
            "avg_loss_dollars": round(avg_loss, 2),
            "largest_win_dollars": round(largest_win, 2),
            "largest_loss_dollars": round(largest_loss, 2),
            "avg_win_pct": round(avg_win / start_equity * 100, 2) if start_equity else 0,
            "avg_loss_pct": round(avg_loss / start_equity * 100, 2) if start_equity else 0,
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "total_fees_dollars": round(total_fees, 2),
            "total_slippage_dollars": round(total_slippage, 2),
            "net_profit_after_fees": round(net_pnl - total_fees, 2),
            "exposure_pct": 0.0,
            "volatility_annualized_pct": round(annual_vol * 100, 2),
            "risk_reward_ratio": round(avg_win / abs(avg_loss), 2) if avg_loss != 0 else 0,
            "expectancy_dollars": round(expectancy, 2),
            "expectancy_pct": round(expectancy_pct, 2),
            "consecutive_wins_max": max_consec_wins,
            "consecutive_losses_max": max_consec_losses,
            "run_duration_days": round(duration_days, 1),
            "positions_opened": total_trades,
            "positions_closed": closed_positions,
            "positions_open": open_positions,
        }

    @staticmethod
    def _empty_metrics(position_count: int = 0) -> dict:
        return {
            "total_return_pct": 0.0, "total_return_dollars": 0.0,
            "annualized_return_pct": 0.0, "time_weighted_return_pct": 0.0,
            "sharpe_ratio": 0.0, "sortino_ratio": 0.0,
            "max_drawdown_pct": 0.0, "max_drawdown_dollars": 0.0,
            "max_drawdown_duration_days": 0, "calmar_ratio": 0.0,
            "win_rate_pct": 0.0, "loss_rate_pct": 0.0, "profit_factor": 0.0,
            "avg_win_dollars": 0.0, "avg_loss_dollars": 0.0,
            "largest_win_dollars": 0.0, "largest_loss_dollars": 0.0,
            "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
            "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
            "total_fees_dollars": 0.0, "total_slippage_dollars": 0.0,
            "net_profit_after_fees": 0.0, "exposure_pct": 0.0,
            "volatility_annualized_pct": 0.0, "risk_reward_ratio": 0.0,
            "expectancy_dollars": 0.0, "expectancy_pct": 0.0,
            "consecutive_wins_max": 0, "consecutive_losses_max": 0,
            "run_duration_days": 0.0, "positions_opened": 0,
            "positions_closed": 0, "positions_open": 0,
        }
