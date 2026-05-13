"""Seed the coordinator database with sample data for demo purposes."""
import asyncio
import json
import math
import random
from datetime import datetime, timedelta, timezone

from coordinator.database.connection import create_engine, create_session_factory
from coordinator.database.models import (
    Base, Account, Algorithm, Worker, AlgorithmInstance, AlgorithmRun,
    Event, AccountCashFlow, AccountSnapshot, BacktestComparison, DecisionLog,
    TradeLog, Position,
)

random.seed(42)


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
            lifetime_metrics={"total_trades": 850, "win_rate": 0.58, "total_pnl": 10680.50,
                              "sharpe_ratio": 1.42, "max_drawdown": -3.2},
        )
        inst2 = AlgorithmInstance(
            algorithm_id=algo2.id, account_id=acct1.id, worker_id=w1.id,
            status="running",
            config_values={"fast_ma": 12, "slow_ma": 26},
            persisted_state={"btc_position": "flat", "eth_position": "long",
                             "eth_entry": 3820.15},
            lifetime_metrics={"total_trades": 320, "win_rate": 0.52, "total_pnl": 4480.00,
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
            lifetime_metrics={"total_trades": 145, "win_rate": 0.73, "total_pnl": 15300.00,
                              "sharpe_ratio": 1.85, "max_drawdown": -4.5},
        )
        inst4 = AlgorithmInstance(
            algorithm_id=algo1.id, account_id=acct3.id, worker_id=w2.id,
            status="stopped",
            config_values={"lookback_period": 15, "std_dev": 1.8, "rsi_threshold": 25},
            lifetime_metrics={"total_trades": 95, "win_rate": 0.61, "total_pnl": 3015.75},
        )
        session.add_all([inst1, inst2, inst3, inst4])
        await session.flush()

        # Lock accounts for running instances
        acct1.locked_by = inst1.id
        acct2.locked_by = inst3.id

        # --- Runs ---
        run1 = AlgorithmRun(
            instance_id=inst1.id, run_number=1, status="completed",
            started_at=now - timedelta(days=90), stopped_at=now - timedelta(days=60),
            starting_equity=50000.0, ending_equity=54200.0,
            net_pnl=4200.0, total_fees=185.40, total_slippage=22.30, trade_count=368,
            metrics={"sharpe": 1.35, "win_rate": 0.56},
        )

        # Extended equity curve for run2 - MeanReversionSPY trending strongly up (60 points)
        def _mr_spy_curve(i, n=60):
            t = i / (n - 1)
            linear = t * 4800
            sine_boost = math.sin(t * math.pi * 2.5) * 350
            noise = (random.random() - 0.4) * 180
            return round(52150 + linear + sine_boost + noise, 2)

        run2 = AlgorithmRun(
            instance_id=inst1.id, run_number=2, status="running",
            started_at=now - timedelta(days=60),
            starting_equity=52150.0,
            net_pnl=4830.50, unrealized_pnl=340.00, total_fees=142.10,
            total_slippage=18.90, trade_count=482,
            metrics={"sharpe": 1.48, "win_rate": 0.59},
            equity_curve=[
                {
                    "timestamp": (now - timedelta(days=60) + timedelta(hours=i * 24)).isoformat(),
                    "equity": _mr_spy_curve(i),
                }
                for i in range(60)
            ],
        )

        # CryptoMomentum - flatter with more volatility (60 points)
        def _crypto_curve(i, n=60):
            t = i / (n - 1)
            linear = t * 1200
            sine1 = math.sin(t * math.pi * 5) * 600
            sine2 = math.sin(t * math.pi * 11) * 250
            noise = (random.random() - 0.5) * 400
            return round(25000 + linear + sine1 + sine2 + noise, 2)

        run3 = AlgorithmRun(
            instance_id=inst2.id, run_number=1, status="running",
            started_at=now - timedelta(days=30),
            starting_equity=25000.0,
            net_pnl=1250.00, unrealized_pnl=180.00, total_fees=85.00,
            total_slippage=42.50, trade_count=189,
            metrics={"sharpe": 0.95, "win_rate": 0.52},
            equity_curve=[
                {
                    "timestamp": (now - timedelta(days=30) + timedelta(hours=i * 12)).isoformat(),
                    "equity": _crypto_curve(i),
                }
                for i in range(60)
            ],
        )

        # IronCondorWeekly - slow steady rise (60 points)
        def _ic_curve(i, n=60):
            t = i / (n - 1)
            linear = t * 6500
            sine_small = math.sin(t * math.pi * 3) * 200
            noise = (random.random() - 0.45) * 120
            return round(100000 + linear + sine_small + noise, 2)

        run4 = AlgorithmRun(
            instance_id=inst3.id, run_number=1, status="running",
            started_at=now - timedelta(days=30),
            starting_equity=100000.0,
            net_pnl=6120.00, unrealized_pnl=-450.00, total_fees=312.00,
            total_slippage=0.0, trade_count=52,
            metrics={"sharpe": 1.85, "win_rate": 0.73},
            equity_curve=[
                {
                    "timestamp": (now - timedelta(days=30) + timedelta(hours=i * 12)).isoformat(),
                    "equity": _ic_curve(i),
                }
                for i in range(60)
            ],
        )
        inst1.active_run_id = None  # will set after flush
        session.add_all([run1, run2, run3, run4])
        await session.flush()

        inst1.active_run_id = run2.id
        inst2.active_run_id = run3.id
        inst3.active_run_id = run4.id

        # --- Cash Flows ---
        for acct, deposits in [(acct1, [(50000, 90), (5000, 45)]),
                                (acct2, [(100000, 90)]),
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

        # --- Account Snapshots (90 days, dramatic upward curve with dips/recoveries) ---
        def _snapshot_curve(day, base_val, peak_move_frac):
            """
            Generates a total_value for a given day (0=oldest, 89=newest).
            Uses a sinusoidal base + linear drift + a noticeable drawdown at days 45-50.
            peak_move_frac is the total fractional gain over 90 days (e.g. 0.20 = 20%).
            """
            t = day / 89.0
            # Linear drift: 0 → peak_move_frac * base_val
            linear = t * peak_move_frac * base_val
            # Slow sinusoidal oscillation (two full cycles)
            slow_wave = math.sin(t * math.pi * 4) * (base_val * 0.025)
            # Drawdown region: days 43-52 → sharp dip and recovery
            dip = 0.0
            if 43 <= day <= 52:
                dip_t = (day - 43) / 9.0  # 0→1 over dip window
                dip = -math.sin(dip_t * math.pi) * (base_val * 0.06)
            # Day-to-day noise
            noise = (random.random() - 0.48) * (base_val * 0.008)
            return round(base_val + linear + slow_wave + dip + noise, 2)

        for acct_id, base_val, cash_frac_center, peak_frac in [
            (acct1.id, 53000, 0.30, 0.18),
            (acct2.id, 106500, 0.40, 0.22),
            (acct3.id, 82500, 0.25, 0.15),
        ]:
            for day in range(90):
                val = _snapshot_curve(day, base_val, peak_frac)
                # Cash fraction varies slightly day-to-day
                cash_frac = cash_frac_center + (random.random() - 0.5) * 0.06
                cash_frac = max(0.15, min(0.60, cash_frac))
                cash = round(val * cash_frac, 2)
                positions_value = round(val - cash, 2)
                session.add(AccountSnapshot(
                    account_id=acct_id,
                    timestamp=now - timedelta(days=89 - day),
                    total_value=val,
                    cash=cash,
                    positions_value=positions_value,
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

        # --- Open Positions (expanded from 3 to 8) ---
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
        # QQQ long
        session.add(Position(
            instance_id=inst1.id, account_id=acct1.id,
            strategy_type="single",
            legs=[{"symbol": "QQQ", "quantity": 30, "side": "long",
                   "avg_price": 462.80, "current_price": 468.15,
                   "asset_type": "equities", "value": 14044.50}],
            status="open", net_cost=13884.0, unrealized_pnl=160.50, total_fees=0.90,
        ))
        # NVDA long
        session.add(Position(
            instance_id=inst1.id, account_id=acct1.id,
            strategy_type="single",
            legs=[{"symbol": "NVDA", "quantity": 20, "side": "long",
                   "avg_price": 875.30, "current_price": 891.40,
                   "asset_type": "equities", "value": 17828.0}],
            status="open", net_cost=17506.0, unrealized_pnl=322.0, total_fees=0.60,
        ))
        # BTC/USD long (small)
        session.add(Position(
            instance_id=inst2.id, account_id=acct1.id,
            strategy_type="single",
            legs=[{"symbol": "BTC/USD", "quantity": 0.12, "side": "long",
                   "avg_price": 66450.00, "current_price": 67820.00,
                   "asset_type": "crypto", "value": 8138.40}],
            status="open", net_cost=7974.0, unrealized_pnl=164.40, total_fees=0.0,
        ))
        # MSFT short
        session.add(Position(
            instance_id=inst4.id, account_id=acct3.id,
            strategy_type="single",
            legs=[{"symbol": "MSFT", "quantity": -15, "side": "short",
                   "avg_price": 415.60, "current_price": 411.25,
                   "asset_type": "equities", "value": -6168.75}],
            status="open", net_cost=-6234.0, unrealized_pnl=65.25, total_fees=0.45,
        ))
        # SPX vertical spread
        session.add(Position(
            instance_id=inst3.id, account_id=acct2.id,
            strategy_type="vertical_spread",
            legs=[
                {"symbol": "SPX 5900C 06/22", "quantity": -2, "side": "short",
                 "avg_price": 6.80, "current_price": 5.40,
                 "asset_type": "options", "value": 1080.0},
                {"symbol": "SPX 5910C 06/22", "quantity": 2, "side": "long",
                 "avg_price": 4.50, "current_price": 3.55,
                 "asset_type": "options", "value": 710.0},
            ],
            status="open", net_cost=-460.0, unrealized_pnl=190.0, total_fees=5.20,
        ))

        # --- Closed positions (last 7 days) for rolling win-rate (~25 positions, ~57% win rate) ---
        closed_7d_data = [
            # Winners (net_pnl > 0) - 14 wins
            (inst1.id, acct1.id, "single",
             [{"symbol": "SPY", "quantity": 100, "side": "long", "avg_price": 582.10,
               "current_price": 585.40, "asset_type": "equities", "value": 58540.0}],
             58210.0, 58540.0, 330.0, 2.40, now - timedelta(days=6, hours=2)),
            (inst1.id, acct1.id, "single",
             [{"symbol": "QQQ", "quantity": 50, "side": "long", "avg_price": 458.20,
               "current_price": 463.50, "asset_type": "equities", "value": 23175.0}],
             22910.0, 23175.0, 265.0, 1.50, now - timedelta(days=6, hours=6)),
            (inst1.id, acct1.id, "single",
             [{"symbol": "AAPL", "quantity": 75, "side": "long", "avg_price": 188.40,
               "current_price": 191.20, "asset_type": "equities", "value": 14340.0}],
             14130.0, 14340.0, 210.0, 2.25, now - timedelta(days=5, hours=3)),
            (inst2.id, acct1.id, "single",
             [{"symbol": "ETH/USD", "quantity": 1.5, "side": "long", "avg_price": 3710.0,
               "current_price": 3850.0, "asset_type": "crypto", "value": 5775.0}],
             5565.0, 5775.0, 210.0, 0.0, now - timedelta(days=5, hours=8)),
            (inst1.id, acct1.id, "single",
             [{"symbol": "NVDA", "quantity": 10, "side": "long", "avg_price": 858.60,
               "current_price": 872.40, "asset_type": "equities", "value": 8724.0}],
             8586.0, 8724.0, 138.0, 0.30, now - timedelta(days=5, hours=12)),
            (inst3.id, acct2.id, "iron_condor",
             [{"symbol": "SPX 5800C 06/01", "quantity": -1, "side": "short", "avg_price": 5.10,
               "current_price": 0.05, "asset_type": "options", "value": 5.0},
              {"symbol": "SPX 5810C 06/01", "quantity": 1, "side": "long", "avg_price": 3.20,
               "current_price": 0.02, "asset_type": "options", "value": 2.0}],
             -190.0, 3.0, 496.0, 5.20, now - timedelta(days=4, hours=1)),
            (inst1.id, acct1.id, "single",
             [{"symbol": "SPY", "quantity": 75, "side": "long", "avg_price": 584.00,
               "current_price": 590.30, "asset_type": "equities", "value": 44272.5}],
             43800.0, 44272.5, 472.5, 2.25, now - timedelta(days=4, hours=5)),
            (inst1.id, acct1.id, "single",
             [{"symbol": "MSFT", "quantity": 25, "side": "long", "avg_price": 408.50,
               "current_price": 415.80, "asset_type": "equities", "value": 10395.0}],
             10212.5, 10395.0, 182.5, 0.75, now - timedelta(days=3, hours=2)),
            (inst2.id, acct1.id, "single",
             [{"symbol": "BTC/USD", "quantity": 0.08, "side": "long", "avg_price": 65200.0,
               "current_price": 67100.0, "asset_type": "crypto", "value": 5368.0}],
             5216.0, 5368.0, 152.0, 0.0, now - timedelta(days=3, hours=7)),
            (inst1.id, acct1.id, "single",
             [{"symbol": "TSLA", "quantity": 20, "side": "long", "avg_price": 176.40,
               "current_price": 183.20, "asset_type": "equities", "value": 3664.0}],
             3528.0, 3664.0, 136.0, 0.60, now - timedelta(days=2, hours=3)),
            (inst3.id, acct2.id, "vertical_spread",
             [{"symbol": "SPX 5850P 06/08", "quantity": -1, "side": "short", "avg_price": 7.20,
               "current_price": 0.10, "asset_type": "options", "value": 10.0},
              {"symbol": "SPX 5840P 06/08", "quantity": 1, "side": "long", "avg_price": 5.00,
               "current_price": 0.05, "asset_type": "options", "value": 5.0}],
             -220.0, 5.0, 615.0, 5.20, now - timedelta(days=2, hours=7)),
            (inst1.id, acct1.id, "single",
             [{"symbol": "AAPL", "quantity": 50, "side": "long", "avg_price": 189.80,
               "current_price": 193.50, "asset_type": "equities", "value": 9675.0}],
             9490.0, 9675.0, 185.0, 1.50, now - timedelta(days=1, hours=4)),
            (inst1.id, acct1.id, "single",
             [{"symbol": "SPY", "quantity": 60, "side": "long", "avg_price": 586.20,
               "current_price": 591.80, "asset_type": "equities", "value": 35508.0}],
             35172.0, 35508.0, 336.0, 1.80, now - timedelta(days=1, hours=9)),
            (inst2.id, acct1.id, "single",
             [{"symbol": "ETH/USD", "quantity": 2.0, "side": "long", "avg_price": 3780.0,
               "current_price": 3895.0, "asset_type": "crypto", "value": 7790.0}],
             7560.0, 7790.0, 230.0, 0.0, now - timedelta(hours=22)),
            # Losers (net_pnl < 0) - 11 losses
            (inst1.id, acct1.id, "single",
             [{"symbol": "SPY", "quantity": 80, "side": "long", "avg_price": 591.30,
               "current_price": 587.80, "asset_type": "equities", "value": 47024.0}],
             47304.0, 47024.0, -280.0, 2.40, now - timedelta(days=6, hours=4)),
            (inst1.id, acct1.id, "single",
             [{"symbol": "TSLA", "quantity": 30, "side": "long", "avg_price": 182.40,
               "current_price": 177.60, "asset_type": "equities", "value": 5328.0}],
             5472.0, 5328.0, -144.0, 0.90, now - timedelta(days=6, hours=9)),
            (inst2.id, acct1.id, "single",
             [{"symbol": "BTC/USD", "quantity": 0.1, "side": "long", "avg_price": 68200.0,
               "current_price": 65900.0, "asset_type": "crypto", "value": 6590.0}],
             6820.0, 6590.0, -230.0, 0.0, now - timedelta(days=5, hours=5)),
            (inst1.id, acct1.id, "single",
             [{"symbol": "NVDA", "quantity": 15, "side": "long", "avg_price": 892.0,
               "current_price": 878.50, "asset_type": "equities", "value": 13177.5}],
             13380.0, 13177.5, -202.5, 0.45, now - timedelta(days=4, hours=8)),
            (inst1.id, acct1.id, "single",
             [{"symbol": "QQQ", "quantity": 40, "side": "long", "avg_price": 468.90,
               "current_price": 462.30, "asset_type": "equities", "value": 18492.0}],
             18756.0, 18492.0, -264.0, 1.20, now - timedelta(days=3, hours=5)),
            (inst2.id, acct1.id, "single",
             [{"symbol": "ETH/USD", "quantity": 1.8, "side": "long", "avg_price": 3920.0,
               "current_price": 3845.0, "asset_type": "crypto", "value": 6921.0}],
             7056.0, 6921.0, -135.0, 0.0, now - timedelta(days=2, hours=10)),
            (inst1.id, acct1.id, "single",
             [{"symbol": "AAPL", "quantity": 60, "side": "long", "avg_price": 194.20,
               "current_price": 190.80, "asset_type": "equities", "value": 11448.0}],
             11652.0, 11448.0, -204.0, 1.80, now - timedelta(days=2, hours=12)),
            (inst3.id, acct2.id, "vertical_spread",
             [{"symbol": "SPX 5960C 06/08", "quantity": -1, "side": "short", "avg_price": 3.80,
               "current_price": 6.20, "asset_type": "options", "value": 620.0},
              {"symbol": "SPX 5970C 06/08", "quantity": 1, "side": "long", "avg_price": 2.10,
               "current_price": 4.50, "asset_type": "options", "value": 450.0}],
             -170.0, -170.0, -240.0, 5.20, now - timedelta(days=1, hours=6)),
            (inst1.id, acct1.id, "single",
             [{"symbol": "MSFT", "quantity": 20, "side": "long", "avg_price": 418.30,
               "current_price": 411.90, "asset_type": "equities", "value": 8238.0}],
             8366.0, 8238.0, -128.0, 0.60, now - timedelta(days=1, hours=11)),
            (inst1.id, acct1.id, "single",
             [{"symbol": "SPY", "quantity": 50, "side": "long", "avg_price": 592.80,
               "current_price": 588.40, "asset_type": "equities", "value": 29420.0}],
             29640.0, 29420.0, -220.0, 1.50, now - timedelta(hours=26)),
            (inst2.id, acct1.id, "single",
             [{"symbol": "BTC/USD", "quantity": 0.06, "side": "long", "avg_price": 69100.0,
               "current_price": 67400.0, "asset_type": "crypto", "value": 4044.0}],
             4146.0, 4044.0, -102.0, 0.0, now - timedelta(hours=18)),
            (inst1.id, acct1.id, "single",
             [{"symbol": "TSLA", "quantity": 25, "side": "long", "avg_price": 181.60,
               "current_price": 178.40, "asset_type": "equities", "value": 4460.0}],
             4540.0, 4460.0, -80.0, 0.75, now - timedelta(hours=14)),
        ]

        for iid, aid, stype, legs, net_cost, net_proceeds, net_pnl, fees, closed_at in closed_7d_data:
            session.add(Position(
                instance_id=iid, account_id=aid,
                strategy_type=stype,
                legs=legs,
                status="closed",
                net_cost=net_cost,
                net_proceeds=net_proceeds,
                net_pnl=net_pnl,
                total_fees=fees,
                closed_at=closed_at,
            ))

        # --- Today's closed positions (for trades_today_wins/losses) ---
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_closed_data = [
            # 6 wins
            (inst1.id, acct1.id, "single",
             [{"symbol": "SPY", "quantity": 40, "side": "long", "avg_price": 588.50,
               "current_price": 592.10, "asset_type": "equities", "value": 23684.0}],
             23540.0, 23684.0, 144.0, 1.20, today_start + timedelta(hours=1, minutes=15)),
            (inst1.id, acct1.id, "single",
             [{"symbol": "QQQ", "quantity": 25, "side": "long", "avg_price": 463.20,
               "current_price": 467.40, "asset_type": "equities", "value": 11685.0}],
             11580.0, 11685.0, 105.0, 0.75, today_start + timedelta(hours=2, minutes=30)),
            (inst1.id, acct1.id, "single",
             [{"symbol": "AAPL", "quantity": 30, "side": "long", "avg_price": 191.80,
               "current_price": 194.60, "asset_type": "equities", "value": 5838.0}],
             5754.0, 5838.0, 84.0, 0.90, today_start + timedelta(hours=3, minutes=45)),
            (inst2.id, acct1.id, "single",
             [{"symbol": "ETH/USD", "quantity": 0.8, "side": "long", "avg_price": 3840.0,
               "current_price": 3912.0, "asset_type": "crypto", "value": 3129.6}],
             3072.0, 3129.6, 57.6, 0.0, today_start + timedelta(hours=2, minutes=10)),
            (inst1.id, acct1.id, "single",
             [{"symbol": "NVDA", "quantity": 8, "side": "long", "avg_price": 878.40,
               "current_price": 888.20, "asset_type": "equities", "value": 7105.6}],
             7027.2, 7105.6, 78.4, 0.24, today_start + timedelta(hours=4, minutes=20)),
            (inst3.id, acct2.id, "vertical_spread",
             [{"symbol": "SPX 5870C 06/22", "quantity": -1, "side": "short", "avg_price": 5.80,
               "current_price": 0.05, "asset_type": "options", "value": 5.0},
              {"symbol": "SPX 5880C 06/22", "quantity": 1, "side": "long", "avg_price": 3.60,
               "current_price": 0.03, "asset_type": "options", "value": 3.0}],
             -220.0, 8.0, 512.0, 5.20, today_start + timedelta(hours=3, minutes=0)),
            # 4 losses
            (inst1.id, acct1.id, "single",
             [{"symbol": "TSLA", "quantity": 15, "side": "long", "avg_price": 183.60,
               "current_price": 179.80, "asset_type": "equities", "value": 2697.0}],
             2754.0, 2697.0, -57.0, 0.45, today_start + timedelta(hours=1, minutes=45)),
            (inst1.id, acct1.id, "single",
             [{"symbol": "SPY", "quantity": 35, "side": "long", "avg_price": 593.20,
               "current_price": 589.40, "asset_type": "equities", "value": 20629.0}],
             20762.0, 20629.0, -133.0, 1.05, today_start + timedelta(hours=4, minutes=50)),
            (inst2.id, acct1.id, "single",
             [{"symbol": "BTC/USD", "quantity": 0.04, "side": "long", "avg_price": 68900.0,
               "current_price": 67200.0, "asset_type": "crypto", "value": 2688.0}],
             2756.0, 2688.0, -68.0, 0.0, today_start + timedelta(hours=3, minutes=30)),
            (inst1.id, acct1.id, "single",
             [{"symbol": "MSFT", "quantity": 12, "side": "long", "avg_price": 416.80,
               "current_price": 412.40, "asset_type": "equities", "value": 4948.8}],
             5001.6, 4948.8, -52.8, 0.36, today_start + timedelta(hours=5, minutes=10)),
        ]

        for iid, aid, stype, legs, net_cost, net_proceeds, net_pnl, fees, closed_at in today_closed_data:
            session.add(Position(
                instance_id=iid, account_id=aid,
                strategy_type=stype,
                legs=legs,
                status="closed",
                net_cost=net_cost,
                net_proceeds=net_proceeds,
                net_pnl=net_pnl,
                total_fees=fees,
                closed_at=closed_at,
            ))

        # --- Trade Log entries (historical, last 14 days) ---
        # Equity symbols with realistic prices
        eq_trades = [
            # SPY trades (inst1) - most trades
            ("SPY", "buy",  100, 582.10, 1.20, inst1.id, acct1.id),
            ("SPY", "sell", 100, 585.40, 1.20, inst1.id, acct1.id),
            ("SPY", "buy",  50,  584.30, 0.60, inst1.id, acct1.id),
            ("SPY", "sell", 50,  588.90, 0.60, inst1.id, acct1.id),
            ("SPY", "buy",  75,  583.50, 0.90, inst1.id, acct1.id),
            ("SPY", "sell", 75,  590.10, 0.90, inst1.id, acct1.id),
            ("SPY", "buy",  60,  586.20, 0.72, inst1.id, acct1.id),
            ("SPY", "sell", 60,  591.80, 0.72, inst1.id, acct1.id),
            ("SPY", "buy",  80,  587.00, 0.96, inst1.id, acct1.id),
            ("SPY", "sell", 80,  592.40, 0.96, inst1.id, acct1.id),
            ("SPY", "buy",  45,  589.30, 0.54, inst1.id, acct1.id),
            ("SPY", "sell", 45,  593.60, 0.54, inst1.id, acct1.id),
            ("SPY", "buy",  90,  585.80, 1.08, inst1.id, acct1.id),
            ("SPY", "sell", 90,  591.20, 1.08, inst1.id, acct1.id),
            ("SPY", "buy",  55,  590.50, 0.66, inst1.id, acct1.id),
            ("SPY", "sell", 55,  594.80, 0.66, inst1.id, acct1.id),
            # QQQ trades (inst1)
            ("QQQ", "buy",  50,  458.20, 0.60, inst1.id, acct1.id),
            ("QQQ", "sell", 50,  463.50, 0.60, inst1.id, acct1.id),
            ("QQQ", "buy",  40,  461.80, 0.48, inst1.id, acct1.id),
            ("QQQ", "sell", 40,  467.30, 0.48, inst1.id, acct1.id),
            ("QQQ", "buy",  30,  463.40, 0.36, inst1.id, acct1.id),
            ("QQQ", "sell", 30,  469.10, 0.36, inst1.id, acct1.id),
            # AAPL trades (inst1)
            ("AAPL", "buy",  75,  188.40, 0.75, inst1.id, acct1.id),
            ("AAPL", "sell", 75,  191.20, 0.75, inst1.id, acct1.id),
            ("AAPL", "buy",  50,  190.60, 0.50, inst1.id, acct1.id),
            ("AAPL", "sell", 50,  193.80, 0.50, inst1.id, acct1.id),
            # NVDA trades (inst1)
            ("NVDA", "buy",  20,  858.60, 0.30, inst1.id, acct1.id),
            ("NVDA", "sell", 20,  872.40, 0.30, inst1.id, acct1.id),
            ("NVDA", "buy",  15,  876.20, 0.23, inst1.id, acct1.id),
            ("NVDA", "sell", 15,  884.50, 0.23, inst1.id, acct1.id),
            # MSFT trades (inst1 + inst4)
            ("MSFT", "buy",  25,  408.50, 0.75, inst1.id, acct1.id),
            ("MSFT", "sell", 25,  415.80, 0.75, inst1.id, acct1.id),
            ("MSFT", "sell", 15,  415.60, 0.45, inst4.id, acct3.id),  # short entry
            # TSLA trades (inst1)
            ("TSLA", "buy",  20,  176.40, 0.60, inst1.id, acct1.id),
            ("TSLA", "sell", 20,  183.20, 0.60, inst1.id, acct1.id),
            ("TSLA", "buy",  30,  179.80, 0.90, inst1.id, acct1.id),
            ("TSLA", "sell", 30,  185.60, 0.90, inst1.id, acct1.id),
        ]
        crypto_trades = [
            # BTC/USD (inst2)
            ("BTC/USD", "buy",  0.05, 65200.0, 0.0, inst2.id, acct1.id),
            ("BTC/USD", "sell", 0.05, 67100.0, 0.0, inst2.id, acct1.id),
            ("BTC/USD", "buy",  0.08, 66400.0, 0.0, inst2.id, acct1.id),
            ("BTC/USD", "sell", 0.08, 68300.0, 0.0, inst2.id, acct1.id),
            ("BTC/USD", "buy",  0.10, 65800.0, 0.0, inst2.id, acct1.id),
            ("BTC/USD", "sell", 0.10, 67200.0, 0.0, inst2.id, acct1.id),
            ("BTC/USD", "buy",  0.06, 67400.0, 0.0, inst2.id, acct1.id),
            ("BTC/USD", "sell", 0.06, 66800.0, 0.0, inst2.id, acct1.id),  # loss trade
            # ETH/USD (inst2)
            ("ETH/USD", "buy",  2.5,  3820.15, 0.0, inst2.id, acct1.id),
            ("ETH/USD", "sell", 2.5,  3892.40, 0.0, inst2.id, acct1.id),
            ("ETH/USD", "buy",  1.5,  3710.0,  0.0, inst2.id, acct1.id),
            ("ETH/USD", "sell", 1.5,  3850.0,  0.0, inst2.id, acct1.id),
            ("ETH/USD", "buy",  2.0,  3780.0,  0.0, inst2.id, acct1.id),
            ("ETH/USD", "sell", 2.0,  3895.0,  0.0, inst2.id, acct1.id),
        ]
        options_trades = [
            # SPX options (inst3)
            ("SPX 5950C 06/15", "sell", 1,  4.20, 1.30, inst3.id, acct2.id),
            ("SPX 5960C 06/15", "buy",  1,  2.80, 1.30, inst3.id, acct2.id),
            ("SPX 5800P 06/15", "sell", 1,  3.50, 1.30, inst3.id, acct2.id),
            ("SPX 5790P 06/15", "buy",  1,  2.10, 1.30, inst3.id, acct2.id),
            ("SPX 5850P 06/08", "sell", 1,  7.20, 1.30, inst3.id, acct2.id),
            ("SPX 5840P 06/08", "buy",  1,  5.00, 1.30, inst3.id, acct2.id),
            ("SPX 5900C 06/22", "sell", 2,  6.80, 1.30, inst3.id, acct2.id),
            ("SPX 5910C 06/22", "buy",  2,  4.50, 1.30, inst3.id, acct2.id),
            ("SPX 5800C 06/01", "sell", 1,  5.10, 1.30, inst3.id, acct2.id),
            ("SPX 5810C 06/01", "buy",  1,  3.20, 1.30, inst3.id, acct2.id),
            ("SPX 5960C 06/08", "sell", 1,  3.80, 1.30, inst3.id, acct2.id),
            ("SPX 5970C 06/08", "buy",  1,  2.10, 1.30, inst3.id, acct2.id),
            ("SPX 5870C 06/22", "sell", 1,  5.80, 1.30, inst3.id, acct2.id),
            ("SPX 5880C 06/22", "buy",  1,  3.60, 1.30, inst3.id, acct2.id),
            ("SPX 5850C 06/29", "sell", 2,  8.40, 1.30, inst3.id, acct2.id),
            ("SPX 5860C 06/29", "buy",  2,  5.90, 1.30, inst3.id, acct2.id),
        ]

        all_historical = eq_trades + crypto_trades + options_trades
        # Distribute over last 14 days (excluding today)
        n_hist = len(all_historical)
        for i, (sym, side, qty, price, fees, iid, aid) in enumerate(all_historical):
            # Spread trades across 14 days with some randomness
            days_back = 14 - (i * 14 // n_hist) - 1
            hour_offset = random.randint(14, 20)  # 9:30-3:30 ET in UTC (UTC+offset)
            minute_offset = random.randint(0, 59)
            ts = now - timedelta(days=days_back + 1, hours=hour_offset, minutes=minute_offset)
            # Ensure it's before today
            if ts >= today_start:
                ts = today_start - timedelta(hours=1 + random.random() * 5)
            atype = "equities" if "USD" not in sym and "SPX" not in sym else (
                "crypto" if "USD" in sym else "options"
            )
            session.add(TradeLog(
                instance_id=iid,
                account_id=aid,
                source="algorithm",
                symbol=sym, side=side, quantity=qty,
                filled_price=price, fees=fees,
                asset_type=atype,
                timestamp=ts,
            ))

        # --- Today's trades (14-18 spread across trading day) ---
        # Trading day in UTC: 9:30 ET = 13:30 UTC, 4:00 PM ET = 20:00 UTC
        today_trades = [
            # inst1 / acct1 — equities (bulk)
            (inst1.id, acct1.id, "SPY",  "buy",   40,    588.50, 0.48,  "equities",  13, 32),
            (inst1.id, acct1.id, "SPY",  "sell",  40,    592.10, 0.48,  "equities",  14, 10),
            (inst1.id, acct1.id, "QQQ",  "buy",   25,    463.20, 0.30,  "equities",  13, 45),
            (inst1.id, acct1.id, "QQQ",  "sell",  25,    467.40, 0.30,  "equities",  15, 5),
            (inst1.id, acct1.id, "AAPL", "buy",   30,    191.80, 0.36,  "equities",  14, 22),
            (inst1.id, acct1.id, "AAPL", "sell",  30,    194.60, 0.36,  "equities",  16, 8),
            (inst1.id, acct1.id, "NVDA", "buy",   8,     878.40, 0.12,  "equities",  13, 55),
            (inst1.id, acct1.id, "NVDA", "sell",  8,     888.20, 0.12,  "equities",  15, 42),
            (inst1.id, acct1.id, "TSLA", "buy",   15,    183.60, 0.18,  "equities",  14, 38),
            (inst1.id, acct1.id, "TSLA", "sell",  15,    179.80, 0.18,  "equities",  16, 25),
            (inst1.id, acct1.id, "SPY",  "buy",   35,    589.40, 0.42,  "equities",  17, 15),
            (inst1.id, acct1.id, "MSFT", "buy",   12,    412.40, 0.14,  "equities",  15, 30),
            (inst1.id, acct1.id, "MSFT", "sell",  12,    416.80, 0.14,  "equities",  17, 50),
            # inst2 / acct1 — crypto
            (inst2.id, acct1.id, "ETH/USD", "buy",  0.8,  3840.00, 0.0, "crypto",   14, 0),
            (inst2.id, acct1.id, "ETH/USD", "sell", 0.8,  3912.00, 0.0, "crypto",   15, 50),
            (inst2.id, acct1.id, "BTC/USD", "buy",  0.04, 67200.00, 0.0, "crypto",  13, 40),
            # inst3 / acct2 — options
            (inst3.id, acct2.id, "SPX 5870C 06/22", "sell", 1, 5.80, 1.30, "options", 13, 35),
            (inst3.id, acct2.id, "SPX 5880C 06/22", "buy",  1, 3.60, 1.30, "options", 13, 37),
        ]
        for iid, aid, sym, side, qty, price, fees, atype, hour_utc, minute_utc in today_trades:
            ts = today_start + timedelta(hours=hour_utc, minutes=minute_utc)
            session.add(TradeLog(
                instance_id=iid,
                account_id=aid,
                source="algorithm",
                symbol=sym, side=side, quantity=qty,
                filled_price=price, fees=fees,
                asset_type=atype,
                timestamp=ts,
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
        print(f"  + cash flows, 90d account snapshots (dramatic curve + dip/recovery)")
        print(f"  + 8 open positions (equities/crypto/options, incl. short MSFT)")
        print(f"  + 25 closed positions (last 7 days, ~57% win rate)")
        print(f"  + 10 closed positions today (6 wins, 4 losses)")
        print(f"  + {len(all_historical)} historical trades (last 14 days)")
        print(f"  + {len(today_trades)} today's trades spread across trading day")
        print(f"  + events + 5 recent alerts + backtest comparisons")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
