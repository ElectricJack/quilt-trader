import React from "react";
import { PortfolioEquityWidget } from "./PortfolioEquityWidget";
import { KpiStripWidget } from "./KpiStripWidget";
import { AlgorithmsWidget } from "./AlgorithmsWidget";
import { OpenPositionsWidget } from "./OpenPositionsWidget";
import { RecentTradesWidget } from "./RecentTradesWidget";
import { AccountBalancesWidget } from "./AccountBalancesWidget";
import { AssetAllocationWidget } from "./AssetAllocationWidget";
import { AlertsWidget } from "./AlertsWidget";

export const WIDGET_REGISTRY: Record<string, React.ComponentType> = {
  "portfolio-equity": PortfolioEquityWidget,
  "kpi-strip": KpiStripWidget,
  "algorithms": AlgorithmsWidget,
  "open-positions": OpenPositionsWidget,
  "recent-trades": RecentTradesWidget,
  "account-balances": AccountBalancesWidget,
  "asset-allocation": AssetAllocationWidget,
  "alerts": AlertsWidget,
};

export const WIDGET_TITLES: Record<string, string> = {
  "portfolio-equity": "Portfolio Equity",
  "kpi-strip": "Today's KPIs",
  "algorithms": "Algorithms",
  "open-positions": "Open Positions",
  "recent-trades": "Recent Trades",
  "account-balances": "Account Balances",
  "asset-allocation": "Asset Allocation",
  "alerts": "Alerts",
};
