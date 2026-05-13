import React from "react";
import { PortfolioValueWidget } from "./PortfolioValueWidget";
import { ActiveAlgorithmsWidget } from "./ActiveAlgorithmsWidget";
import { TodaysPnLWidget } from "./TodaysPnLWidget";
import { RecentTradesWidget } from "./RecentTradesWidget";
import { OpenPositionsWidget } from "./OpenPositionsWidget";
import { WorkerHealthWidget } from "./WorkerHealthWidget";
import { BacktestAlertsWidget } from "./BacktestAlertsWidget";
import { SystemEventsWidget } from "./SystemEventsWidget";
import { AccountBalancesWidget } from "./AccountBalancesWidget";

export const WIDGET_REGISTRY: Record<string, React.ComponentType> = {
  "portfolio-value": PortfolioValueWidget,
  "active-algorithms": ActiveAlgorithmsWidget,
  "todays-pnl": TodaysPnLWidget,
  "recent-trades": RecentTradesWidget,
  "open-positions": OpenPositionsWidget,
  "worker-health": WorkerHealthWidget,
  "backtest-alerts": BacktestAlertsWidget,
  "system-events": SystemEventsWidget,
  "account-balances": AccountBalancesWidget,
};

export const WIDGET_TITLES: Record<string, string> = {
  "portfolio-value": "Portfolio Overview",
  "active-algorithms": "Active Algorithms",
  "todays-pnl": "Lifetime P&L",
  "recent-trades": "Recent Trades",
  "open-positions": "Open Positions",
  "worker-health": "Worker Health",
  "backtest-alerts": "Backtest Alerts",
  "system-events": "System Events",
  "account-balances": "Account Balances",
};
