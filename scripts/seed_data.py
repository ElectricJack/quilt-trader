"""Seed the coordinator database with sample data for demo purposes."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

from coordinator.database.connection import create_engine, create_session_factory
from coordinator.database.models import (
    Base, Account, Algorithm, Worker, AlgorithmInstance, AlgorithmRun,
    Event, AccountCashFlow, AccountSnapshot, BacktestComparison, DecisionLog,
    TradeLog, Position,
)


async def seed():
    engine = create_engine("sqlite+aiosqlite:///data/quilt_trader.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    session_factory = create_session_factory(engine)
    now = datetime.now(timezone.utc)

    async with session_factory() as session:
        # --- Accounts ---
        acct1 = Account(
            name="Alpaca Paper (Main)",
            broker_type="alpaca",
            credentials="{}",
            supported_asset_types=["equities", "crypto"],
            options_level=None,
            account_features=["fractional_shares"],
            pdt_mode="warn",
        )
        acct2 = Account(
            name="Alpaca Paper (Options)",
            broker_type="alpaca",
            credentials="{}",
            supported_asset_types=["equities", "options"],
            options_level=2,
            account_features=["options_trading"],
            pdt_mode="block",
        )
        acct3 = Account(
            name="IBKR Live",
            broker_type="interactive_brokers",
            credentials="{}",
            supported_asset_types=["equities", "options", "futures"],
            options_level=3,
            account_features=["margin", "options_trading", "futures_trading"],
            pdt_mode="off",
        )
        session.add_all([acct1, acct2, acct3])
        await session.flush()

        # --- Workers ---
        w1 = Worker(name="pi-alpha", tailscale_ip="100.64.0.10", status="online",
                    last_heartbeat=now - timedelta(seconds=15), max_algorithms=2)
        w2 = Worker(name="pi-beta", tailscale_ip="100.64.0.11", status="online",
                    last_heartbeat=now - timedelta(seconds=8), max_algorithms=2)
        w3 = Worker(name="pi-gamma", tailscale_ip="100.64.0.12", status="offline",
                    last_heartbeat=now - timedelta(hours=3), max_algorithms=1)
        session.add_all([w1, w2, w3])
        await session.flush()

        # --- Algorithms ---
        algo1 = Algorithm(
            repo_url="https://github.com/jkern/mean-reversion-spy",
            name="MeanReversionSPY",
            description="Mean reversion strategy on SPY using Bollinger Bands and RSI",
            version="1.2.0",
            commit_hash="a1b2c3d",
            required_asset_types=["equities"],
            supported_brokers=["alpaca", "interactive_brokers"],
            config_schema={"lookback_period": {"type": "integer", "default": 20},
                           "std_dev": {"type": "number", "default": 2.0},
                           "rsi_threshold": {"type": "integer", "default": 30}},
            install_status="installed",
        )
        algo2 = Algorithm(
            repo_url="https://github.com/jkern/momentum-crypto",
            name="CryptoMomentum",
            description="Momentum-based crypto strategy trading BTC and ETH",
            version="0.8.1",
            commit_hash="e5f6g7h",
            required_asset_types=["crypto"],
            supported_brokers=["alpaca"],
            config_schema={"fast_ma": {"type": "integer", "default": 12},
                           "slow_ma": {"type": "integer", "default": 26}},
            install_status="installed",
        )
        algo3 = Algorithm(
            repo_url="https://github.com/jkern/iron-condor-weekly",
            name="IronCondorWeekly",
            description="Weekly iron condor strategy on SPX",
            version="2.0.0",
            commit_hash="i8j9k0l",
            required_asset_types=["options"],
            required_options_level=2,
            supported_brokers=["alpaca", "interactive_brokers"],
            install_status="installed",
        )
        algo4 = Algorithm(
            repo_url="https://github.com/jkern/pairs-trader",
            name="PairsTrader",
            description="Statistical arbitrage pairs trading (KO/PEP, GOOG/GOOGL)",
            version="0.3.0",
            commit_hash="m1n2o3p",
            required_asset_types=["equities"],
            supported_brokers=["alpaca", "interactive_brokers"],
            install_status="installing",
            install_error=None,
        )
        session.add_all([algo1, algo2, algo3, algo4])
        await session.flush()

        # --- Instances ---
        inst1 = AlgorithmInstance(
            algorithm_id=algo1.id, account_id=acct1.id, worker_id=w1.id,
            status="running",
            config_values={"lookback_period": 20, "std_dev": 2.0, "rsi_threshold": 30},
            persisted_state={"last_signal_time": (now - timedelta(hours=2)).isoformat(),
                             "current_position": "long", "entry_price": 587.42},
            lifetime_metrics={"total_trades": 342, "win_rate": 0.58, "total_pnl": 4280.50,
                              "sharpe_ratio": 1.42, "max_drawdown": -3.2},
        )
        inst2 = AlgorithmInstance(
            algorithm_id=algo2.id, account_id=acct1.id, worker_id=w1.id,
            status="running",
            config_values={"fast_ma": 12, "slow_ma": 26},
            persisted_state={"btc_position": "flat", "eth_position": "long",
                             "eth_entry": 3820.15},
            lifetime_metrics={"total_trades": 89, "win_rate": 0.52, "total_pnl": 1250.00,
                              "sharpe_ratio": 0.95, "max_drawdown": -8.1},
        )
        inst3 = AlgorithmInstance(
            algorithm_id=algo3.id, account_id=acct2.id, worker_id=w2.id,
            status="running",
            config_values={"width": 10, "delta_target": 0.15},
            persisted_state={"open_spreads": [
                {"symbol": "SPX", "expiry": (now + timedelta(days=3)).strftime("%Y-%m-%d"),
                 "short_call": 5950, "long_call": 5960, "short_put": 5800, "long_put": 5790}
            ]},
            lifetime_metrics={"total_trades": 52, "win_rate": 0.73, "total_pnl": 6120.00,
                              "sharpe_ratio": 1.85, "max_drawdown": -4.5},
        )
        inst4 = AlgorithmInstance(
            algorithm_id=algo1.id, account_id=acct3.id, worker_id=w2.id,
            status="stopped",
            config_values={"lookback_period": 15, "std_dev": 1.8, "rsi_threshold": 25},
            lifetime_metrics={"total_trades": 28, "win_rate": 0.61, "total_pnl": 890.30},
        )
        session.add_all([inst1, inst2, inst3, inst4])
        await session.flush()

        # Lock accounts for running instances
        acct1.locked_by = inst1.id
        acct2.locked_by = inst3.id

        # --- Runs ---
        run1 = AlgorithmRun(
            instance_id=inst1.id, run_number=1, status="completed",
            started_at=now - timedelta(days=30), stopped_at=now - timedelta(days=15),
            starting_equity=50000.0, ending_equity=52150.0,
            net_pnl=2150.0, total_fees=85.40, total_slippage=12.30, trade_count=168,
            metrics={"sharpe": 1.35, "win_rate": 0.56},
        )
        run2 = AlgorithmRun(
            instance_id=inst1.id, run_number=2, status="running",
            started_at=now - timedelta(days=14),
            starting_equity=52150.0,
            net_pnl=2130.50, unrealized_pnl=340.00, total_fees=62.10,
            total_slippage=8.90, trade_count=174,
            metrics={"sharpe": 1.48, "win_rate": 0.59},
            equity_curve=[
                {
                    "timestamp": (now - timedelta(days=14) + timedelta(hours=i * 17)).isoformat(),
                    "equity": round(52150 + i * 161 + ((-1) ** i) * 85, 2),
                }
                for i in range(20)
            ],
        )
        run3 = AlgorithmRun(
            instance_id=inst2.id, run_number=1, status="running",
            started_at=now - timedelta(days=7),
            starting_equity=25000.0,
            net_pnl=1250.00, unrealized_pnl=180.00, total_fees=45.00,
            total_slippage=22.50, trade_count=89,
            metrics={"sharpe": 0.95, "win_rate": 0.52},
            equity_curve=[
                {
                    "timestamp": (now - timedelta(days=7) + timedelta(hours=i * 8)).isoformat(),
                    "equity": round(25000 + i * 66 + ((-1) ** i) * 120, 2),
                }
                for i in range(20)
            ],
        )
        run4 = AlgorithmRun(
            instance_id=inst3.id, run_number=1, status="running",
            started_at=now - timedelta(days=7),
            starting_equity=100000.0,
            net_pnl=6120.00, unrealized_pnl=-450.00, total_fees=312.00,
            total_slippage=0.0, trade_count=52,
            metrics={"sharpe": 1.85, "win_rate": 0.73},
            equity_curve=[
                {
                    "timestamp": (now - timedelta(days=7) + timedelta(hours=i * 8)).isoformat(),
                    "equity": round(100000 + i * 328 + ((-1) ** i) * 250, 2),
                }
                for i in range(20)
            ],
        )
        inst1.active_run_id = None  # will set after flush
        session.add_all([run1, run2, run3, run4])
        await session.flush()

        inst1.active_run_id = run2.id
        inst2.active_run_id = run3.id
        inst3.active_run_id = run4.id

        # --- Cash Flows ---
        for acct, deposits in [(acct1, [(50000, 45), (5000, 20)]),
                                (acct2, [(100000, 60)]),
                                (acct3, [(75000, 90), (10000, 30)])]:
            for amount, days_ago in deposits:
                session.add(AccountCashFlow(
                    account_id=acct.id, type="deposit", amount=amount,
                    timestamp=now - timedelta(days=days_ago),
                    notes="Initial funding" if days_ago > 40 else "Top-up",
                ))

        session.add(AccountCashFlow(
            account_id=acct1.id, type="withdrawal", amount=-2000,
            timestamp=now - timedelta(days=5), notes="Profit withdrawal",
        ))

        # --- Account Snapshots ---
        for acct_id, base_val, cash_frac in [
            (acct1.id, 53000, 0.3),
            (acct2.id, 106500, 0.4),
            (acct3.id, 82500, 0.25),
        ]:
            for day in range(30):
                val = base_val + (day * 45) + ((-1)**day * 120)
                session.add(AccountSnapshot(
                    account_id=acct_id,
                    timestamp=now - timedelta(days=30-day),
                    total_value=val,
                    cash=val * cash_frac,
                    positions_value=val * (1 - cash_frac),
                    net_deposits_cumulative=base_val - 3000,
                    source="broker_sync",
                ))

        # --- Events ---
        events_data = [
            ("system", None, "coordinator_started", "info", {"version": "0.1.0"}, 120),
            ("worker", None, "worker_connected", "info", {"worker": "pi-alpha", "ip": "100.64.0.10"}, 115),
            ("worker", None, "worker_connected", "info", {"worker": "pi-beta", "ip": "100.64.0.11"}, 114),
            ("instance", inst1.id, "instance_started", "info", {"algorithm": "MeanReversionSPY"}, 110),
            ("instance", inst2.id, "instance_started", "info", {"algorithm": "CryptoMomentum"}, 108),
            ("instance", inst3.id, "instance_started", "info", {"algorithm": "IronCondorWeekly"}, 100),
            ("instance", inst1.id, "trade_executed", "info",
             {"symbol": "SPY", "side": "buy", "qty": 50, "price": 587.42}, 60),
            ("instance", inst1.id, "trade_executed", "info",
             {"symbol": "SPY", "side": "sell", "qty": 50, "price": 589.10}, 30),
            ("instance", inst2.id, "trade_executed", "info",
             {"symbol": "ETH/USD", "side": "buy", "qty": 2.5, "price": 3820.15}, 45),
            ("instance", inst3.id, "trade_executed", "info",
             {"symbol": "SPX 5950C", "side": "sell", "qty": 1, "price": 4.20}, 20),
            ("system", None, "pdt_warning", "warning",
             {"account": "Alpaca Paper (Main)", "day_trades_5d": 2, "remaining": 1}, 15),
            ("instance", inst2.id, "signal_rejected", "warning",
             {"reason": "Insufficient buying power", "symbol": "BTC/USD"}, 5),
            ("worker", None, "worker_disconnected", "warning", {"worker": "pi-gamma"}, 180),
            ("system", None, "backtest_divergence", "error",
             {"algorithm": "CryptoMomentum", "match_pct": 87.5, "threshold": 95.0}, 2),
        ]
        for src_type, src_id, evt_type, severity, payload, mins_ago in events_data:
            session.add(Event(
                source_type=src_type, source_id=src_id, event_type=evt_type,
                severity=severity, payload=payload,
                timestamp=now - timedelta(minutes=mins_ago),
                routed_to_discord=severity in ("warning", "error"),
                discord_channel="alerts" if severity == "error" else None,
            ))

        # --- Backtest Comparisons ---
        session.add(BacktestComparison(
            instance_id=inst1.id, algorithm_id=algo1.id,
            time_range_start=now - timedelta(days=1),
            time_range_end=now,
            total_ticks=390, matching_ticks=385,
            match_percentage=98.72,
            divergences=[
                {"timestamp": (now - timedelta(hours=4)).isoformat(),
                 "reason": "Signal mismatch", "live_signals": [{"legs": [{"symbol": "SPY", "signal_type": "buy"}]}],
                 "backtest_signals": []},
            ],
            summary="Match rate: 98.72%",
        ))
        session.add(BacktestComparison(
            instance_id=inst2.id, algorithm_id=algo2.id,
            time_range_start=now - timedelta(days=1),
            time_range_end=now,
            total_ticks=1440, matching_ticks=1260,
            match_percentage=87.5,
            divergences=[
                {"timestamp": (now - timedelta(hours=i)).isoformat(),
                 "reason": "Signal mismatch"} for i in range(5)
            ],
            summary="ALERT: Match rate: 87.5%",
        ))
        session.add(BacktestComparison(
            instance_id=inst3.id, algorithm_id=algo3.id,
            time_range_start=now - timedelta(days=1),
            time_range_end=now,
            total_ticks=78, matching_ticks=78,
            match_percentage=100.0,
            summary="Match rate: 100.0%",
        ))

        # --- Some Positions ---
        session.add(Position(
            instance_id=inst1.id, account_id=acct1.id,
            strategy_type="single",
            legs=[{"symbol": "SPY", "quantity": 50, "side": "long",
                   "avg_price": 587.42, "current_price": 589.80,
                   "asset_type": "equities", "value": 29490.0}],
            status="open", net_cost=29371.0, unrealized_pnl=119.0, total_fees=1.20,
        ))
        session.add(Position(
            instance_id=inst2.id, account_id=acct1.id,
            strategy_type="single",
            legs=[{"symbol": "ETH/USD", "quantity": 2.5, "side": "long",
                   "avg_price": 3820.15, "current_price": 3892.40,
                   "asset_type": "crypto", "value": 9731.0}],
            status="open", net_cost=9550.38, unrealized_pnl=180.63, total_fees=0.0,
        ))
        session.add(Position(
            instance_id=inst3.id, account_id=acct2.id,
            strategy_type="iron_condor",
            legs=[
                {"symbol": "SPX 5950C 06/15", "quantity": -1, "side": "short",
                 "avg_price": 4.20, "current_price": 3.10,
                 "asset_type": "options", "value": 310.0},
                {"symbol": "SPX 5960C 06/15", "quantity": 1, "side": "long",
                 "avg_price": 2.80, "current_price": 2.00,
                 "asset_type": "options", "value": 200.0},
                {"symbol": "SPX 5800P 06/15", "quantity": -1, "side": "short",
                 "avg_price": 3.50, "current_price": 2.90,
                 "asset_type": "options", "value": 290.0},
                {"symbol": "SPX 5790P 06/15", "quantity": 1, "side": "long",
                 "avg_price": 2.10, "current_price": 1.80,
                 "asset_type": "options", "value": 180.0},
            ],
            status="open", net_cost=-280.0, unrealized_pnl=150.0, total_fees=5.20,
        ))
        session.add(Position(
            instance_id=inst1.id, account_id=acct1.id,
            strategy_type="single",
            legs=[{"symbol": "SPY", "quantity": 100, "side": "long",
                   "avg_price": 582.10, "current_price": 585.40,
                   "asset_type": "equities", "value": 58540.0}],
            status="closed", net_cost=58210.0, net_proceeds=58540.0,
            net_pnl=330.0, total_fees=2.40,
            closed_at=now - timedelta(days=3),
        ))

        # --- Trade Log entries (historical, last 24 h) ---
        for i, (sym, side, qty, price, fees) in enumerate([
            ("SPY", "buy", 50, 587.42, 0.60),
            ("SPY", "sell", 100, 585.40, 1.20),
            ("SPY", "buy", 100, 582.10, 1.20),
            ("ETH/USD", "buy", 2.5, 3820.15, 0.0),
            ("SPX 5950C", "sell", 1, 4.20, 1.30),
            ("SPX 5960C", "buy", 1, 2.80, 1.30),
            ("SPX 5800P", "sell", 1, 3.50, 1.30),
            ("SPX 5790P", "buy", 1, 2.10, 1.30),
        ]):
            session.add(TradeLog(
                instance_id=inst1.id if i < 3 else (inst2.id if i == 3 else inst3.id),
                account_id=acct1.id if i < 4 else acct2.id,
                source="algorithm",
                symbol=sym, side=side, quantity=qty,
                filled_price=price, fees=fees,
                asset_type="equities" if "SPY" in sym else ("crypto" if "USD" in sym else "options"),
                timestamp=now - timedelta(hours=24-i*3),
            ))

        # --- Today's trades (for KPI trades_today count and instance today_pnl) ---
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_trades = [
            # inst1 / acct1 — equities
            (inst1.id, acct1.id, "SPY", "buy",  25, 590.10, 0.30, "equities",  0.5),
            (inst1.id, acct1.id, "SPY", "sell", 25, 592.75, 0.30, "equities",  1.5),
            # inst2 / acct1 — crypto
            (inst2.id, acct1.id, "BTC/USD", "buy", 0.05, 67250.00, 0.0, "crypto",  2.0),
            # inst3 / acct2 — options
            (inst3.id, acct2.id, "SPX 5850P 06/22", "sell", 1, 6.80, 1.30, "options", 0.5),
            (inst3.id, acct2.id, "SPX 5840P 06/22", "buy",  1, 4.50, 1.30, "options", 0.75),
        ]
        for j, (iid, aid, sym, side, qty, price, fees, atype, h_offset) in enumerate(today_trades):
            session.add(TradeLog(
                instance_id=iid,
                account_id=aid,
                source="algorithm",
                symbol=sym, side=side, quantity=qty,
                filled_price=price, fees=fees,
                asset_type=atype,
                timestamp=today_start + timedelta(hours=h_offset + j * 0.5),
            ))

        # --- Extra recent warning/error events (within last 24 h, for alerts widget) ---
        recent_alert_events = [
            ("instance", inst1.id, "risk_limit_approached", "warning",
             {"symbol": "SPY", "current_exposure_pct": 78.4, "limit_pct": 80.0}, 45),
            ("instance", inst3.id, "order_rejected", "error",
             {"symbol": "SPX 5900C 06/22", "reason": "Margin insufficient", "required": 3500}, 90),
            ("worker", w2.id, "high_cpu_usage", "warning",
             {"worker": "pi-beta", "cpu_pct": 91.2, "threshold_pct": 85.0}, 130),
            ("instance", inst2.id, "slippage_exceeded", "warning",
             {"symbol": "BTC/USD", "expected_price": 67100.00, "filled_price": 67250.00,
              "slippage_pct": 0.22}, 200),
            ("system", None, "db_connection_retry", "warning",
             {"attempt": 2, "max_attempts": 5, "delay_s": 5}, 320),
        ]
        for src_type, src_id, evt_type, severity, payload, mins_ago in recent_alert_events:
            session.add(Event(
                source_type=src_type, source_id=src_id, event_type=evt_type,
                severity=severity, payload=payload,
                timestamp=now - timedelta(minutes=mins_ago),
                routed_to_discord=True,
                discord_channel="alerts" if severity == "error" else None,
            ))

        # --- Extra BacktestComparison with low match (shows up in alerts) ---
        session.add(BacktestComparison(
            instance_id=inst3.id, algorithm_id=algo3.id,
            time_range_start=now - timedelta(hours=12),
            time_range_end=now,
            total_ticks=200, matching_ticks=148,
            match_percentage=74.0,
            divergences=[
                {"timestamp": (now - timedelta(hours=i)).isoformat(),
                 "reason": "Delta mismatch"} for i in range(1, 4)
            ],
            summary="ALERT: Match rate: 74.0% — below 90% threshold",
        ))

        await session.commit()
        print(f"Seeded: 3 accounts, 3 workers, 4 algorithms, 4 instances, 4 runs")
        print(f"  + cash flows, 30d account snapshots, events + 5 recent alerts")
        print(f"  + backtest comparisons (2 with <90% match), positions with value+asset_type legs")
        print(f"  + 8 historical trades + 5 today's trades for KPI/sparkline population")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
