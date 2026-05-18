import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    ForeignKey,
    JSON,
    Date,
    UniqueConstraint,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    broker_type: Mapped[str] = mapped_column(String, nullable=False)
    environment: Mapped[str] = mapped_column(String, nullable=False, default="paper")
    credentials: Mapped[str] = mapped_column(Text, nullable=False)
    supported_asset_types: Mapped[list] = mapped_column(JSON, nullable=False)
    options_level: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    account_features: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    pdt_mode: Mapped[str] = mapped_column(String, nullable=False, default="off")
    locked_by: Mapped[Optional[str]] = mapped_column(String, ForeignKey("algorithm_instances.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    instances: Mapped[list["AlgorithmInstance"]] = relationship(back_populates="account", foreign_keys="AlgorithmInstance.account_id")
    cash_flows: Mapped[list["AccountCashFlow"]] = relationship(back_populates="account")
    snapshots: Mapped[list["AccountSnapshot"]] = relationship(back_populates="account")


class Algorithm(Base):
    __tablename__ = "algorithms"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    repo_url: Mapped[str] = mapped_column(String, nullable=False)
    source_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    commit_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    required_asset_types: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    required_options_level: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    required_account_features: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    supported_brokers: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    data_dependencies: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    config_schema: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    custom_events: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    install_status: Mapped[str] = mapped_column(String, nullable=False, default="installed")
    install_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    instances: Mapped[list["AlgorithmInstance"]] = relationship(back_populates="algorithm")


class Worker(Base):
    __tablename__ = "workers"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    name: Mapped[str] = mapped_column(String, nullable=False)
    tailscale_ip: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="offline")
    last_heartbeat: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    max_algorithms: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    install_token: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True, unique=True)
    install_status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    instances: Mapped[list["AlgorithmInstance"]] = relationship(back_populates="worker")


class AlgorithmInstance(Base):
    __tablename__ = "algorithm_instances"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    algorithm_id: Mapped[str] = mapped_column(String, ForeignKey("algorithms.id"), nullable=False)
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id"), nullable=False)
    worker_id: Mapped[str] = mapped_column(String, ForeignKey("workers.id"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="stopped")
    active_run_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("algorithm_runs.id"), nullable=True)
    config_values: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    persisted_state: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    state_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    lifetime_metrics: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    algorithm: Mapped["Algorithm"] = relationship(back_populates="instances")
    account: Mapped["Account"] = relationship(back_populates="instances", foreign_keys=[account_id])
    worker: Mapped["Worker"] = relationship(back_populates="instances")
    runs: Mapped[list["AlgorithmRun"]] = relationship(
        back_populates="instance",
        foreign_keys="AlgorithmRun.instance_id",
        cascade="all, delete-orphan",
    )


class Scraper(Base):
    __tablename__ = "scrapers"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    repo_url: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    version: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    commit_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    schedule: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    output_format: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    output_filename: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="stopped")
    dependent_algorithm_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_success: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class AlgorithmRun(Base):
    __tablename__ = "algorithm_runs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    instance_id: Mapped[str] = mapped_column(String, ForeignKey("algorithm_instances.id"), nullable=False)
    run_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    stopped_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    starting_equity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ending_equity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    net_pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    unrealized_pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_fees: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_slippage: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metrics: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    equity_curve: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    instance: Mapped["AlgorithmInstance"] = relationship(back_populates="runs", foreign_keys=[instance_id])


class TradeLog(Base):
    __tablename__ = "trade_log"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    group_id: Mapped[str] = mapped_column(String, nullable=False, default=_new_uuid)
    instance_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("algorithm_instances.id"), nullable=True)
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id"), nullable=False)
    position_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("positions.id"), nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    asset_type: Mapped[str] = mapped_column(String, nullable=False, default="equities")
    side: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    order_type: Mapped[str] = mapped_column(String, nullable=False, default="market")
    requested_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    filled_price: Mapped[float] = mapped_column(Float, nullable=False)
    fees: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    fee_breakdown: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    slippage: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_day_trade: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    broker_txn_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)


class DecisionLog(Base):
    __tablename__ = "decision_log"
    __table_args__ = (
        Index("ix_decision_log_instance_mode_ts", "instance_id", "mode", "timestamp"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    instance_id: Mapped[str] = mapped_column(String, ForeignKey("algorithm_instances.id"), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    tick_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    signals_produced: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    reasoning: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    data_sources_used: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    source_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    severity: Mapped[str] = mapped_column(String, nullable=False, default="info")
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    routed_to_discord: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    discord_channel: Mapped[Optional[str]] = mapped_column(String, nullable=True)


class DataSource(Base):
    __tablename__ = "data_sources"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    type: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_updated: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)


class BacktestComparison(Base):
    __tablename__ = "backtest_comparisons"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    instance_id: Mapped[str] = mapped_column(String, ForeignKey("algorithm_instances.id"), nullable=False)
    algorithm_id: Mapped[str] = mapped_column(String, ForeignKey("algorithms.id"), nullable=False)
    time_range_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    time_range_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total_ticks: Mapped[int] = mapped_column(Integer, nullable=False)
    matching_ticks: Mapped[int] = mapped_column(Integer, nullable=False)
    match_percentage: Mapped[float] = mapped_column(Float, nullable=False)
    divergences: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class PDTTracking(Base):
    __tablename__ = "pdt_tracking"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id"), nullable=False)
    trade_id: Mapped[str] = mapped_column(String, ForeignKey("trade_log.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    open_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    close_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    day_trade_date: Mapped[datetime] = mapped_column(Date, nullable=False)


class MarketDataDownload(Base):
    __tablename__ = "market_data_downloads"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    symbols: Mapped[list] = mapped_column(JSON, nullable=False)
    date_range_start: Mapped[datetime] = mapped_column(Date, nullable=False)
    date_range_end: Mapped[datetime] = mapped_column(Date, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    data_type: Mapped[str] = mapped_column(String, nullable=False, default="bars")
    timeframe: Mapped[str] = mapped_column(String, nullable=False, default="1day")
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    progress_current: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    progress_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    current_symbol_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class DataArchival(Base):
    __tablename__ = "data_archival"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    table_name: Mapped[str] = mapped_column(String, nullable=False)
    date_range_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    date_range_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    archived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Position(Base):
    __tablename__ = "positions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    instance_id: Mapped[Optional[str]] = mapped_column(String, ForeignKey("algorithm_instances.id"), nullable=True)
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id"), nullable=False)
    strategy_type: Mapped[str] = mapped_column(String, nullable=False, default="single")
    legs: Mapped[list] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="open")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    open_group_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    close_group_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    net_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    net_proceeds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    net_pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    unrealized_pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_fees: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    adjustments: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)


class AccountCashFlow(Base):
    __tablename__ = "account_cash_flows"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id"), nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    broker_txn_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    account: Mapped["Account"] = relationship(back_populates="cash_flows")


class AccountSnapshot(Base):
    __tablename__ = "account_snapshots"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id"), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    total_value: Mapped[float] = mapped_column(Float, nullable=False)
    cash: Mapped[float] = mapped_column(Float, nullable=False)
    positions_value: Mapped[float] = mapped_column(Float, nullable=False)
    net_deposits_cumulative: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    source: Mapped[str] = mapped_column(String, nullable=False)
    account: Mapped["Account"] = relationship(back_populates="snapshots")


class LiveSubscription(Base):
    __tablename__ = "live_subscriptions"
    __table_args__ = (
        UniqueConstraint("broker", "symbol", name="uq_live_subscription_broker_symbol"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    broker: Mapped[str] = mapped_column(String, nullable=False)
    symbol: Mapped[str] = mapped_column(String, nullable=False)
    asset_class: Mapped[str] = mapped_column(String, nullable=False, default="equities")
    status: Mapped[str] = mapped_column(String, nullable=False, default="stopped")
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_tick_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    tick_rate_per_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    tick_retention_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=168)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    consumers: Mapped[list["SubscriptionConsumer"]] = relationship(
        back_populates="subscription",
        cascade="all, delete-orphan",
    )


class SubscriptionConsumer(Base):
    __tablename__ = "subscription_consumers"
    __table_args__ = (
        UniqueConstraint(
            "subscription_id", "consumer_type", "consumer_id",
            name="uq_subscription_consumer",
        ),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    subscription_id: Mapped[str] = mapped_column(
        String, ForeignKey("live_subscriptions.id", ondelete="CASCADE"), nullable=False,
    )
    consumer_type: Mapped[str] = mapped_column(String, nullable=False)  # 'manual' | 'algo'
    consumer_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    subscription: Mapped["LiveSubscription"] = relationship(back_populates="consumers")


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    algorithm_id: Mapped[str] = mapped_column(String, ForeignKey("algorithms.id"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="queued")
    # queued | downloading_data | running | completed | failed | cancelled

    # Inputs
    date_range_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    date_range_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    initial_cash: Mapped[float] = mapped_column(Float, nullable=False, default=100_000.0)
    config_overrides: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    buy_trading_fees: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    sell_trading_fees: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    slippage_model: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    benchmark_symbol: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    benchmark_source: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Progress
    progress_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    progress_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Results
    total_return: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cagr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    volatility: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sharpe_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sortino_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    calmar_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_drawdown: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_drawdown_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    romad: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_fees_paid: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_slippage_dollars: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    trade_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    win_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    profit_factor: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    avg_win: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    avg_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    expectancy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longest_drawdown_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    longest_winning_streak: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    longest_losing_streak: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Large blobs
    equity_curve: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    trades: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    drawdown_periods: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    key_metrics: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    rolling_metrics: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    monthly_returns_matrix: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    eoy_returns: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    benchmark_equity_curve: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    drawdown_curve: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    # Side artifacts
    download_ids: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class WorkerActivity(Base):
    __tablename__ = "worker_activity"
    __table_args__ = (
        Index("ix_worker_activity_worker_ts", "worker_id", "timestamp"),
        Index("ix_worker_activity_instance_ts", "instance_id", "timestamp"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_new_uuid)
    worker_id: Mapped[str] = mapped_column(String, ForeignKey("workers.id"), nullable=False)
    instance_id: Mapped[Optional[str]] = mapped_column(
        String, ForeignKey("algorithm_instances.id"), nullable=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    kind: Mapped[str] = mapped_column(String, nullable=False)  # "event" | "log"
    event_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    severity: Mapped[str] = mapped_column(String, nullable=False, default="info")
    logger_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class AlgorithmDeploymentReport(Base):
    __tablename__ = "algorithm_deployment_reports"
    deployment_id: Mapped[str] = mapped_column(
        String, ForeignKey("algorithm_instances.id"), primary_key=True
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    # Scalar metrics — mirror BacktestRun
    total_return: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cagr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    volatility: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sharpe_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sortino_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    calmar_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_drawdown: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_drawdown_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    romad: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_fees_paid: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_slippage_dollars: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    trade_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    win_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    profit_factor: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    avg_win: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    avg_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    expectancy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longest_drawdown_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    longest_winning_streak: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    longest_losing_streak: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Blob columns
    equity_curve: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    drawdown_curve: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    drawdown_periods: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    key_metrics: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    rolling_metrics: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    monthly_returns_matrix: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    eoy_returns: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    runs_index: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
