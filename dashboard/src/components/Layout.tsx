import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  Wallet,
  Bot,
  Server,
  Database,
  FlaskConical,
  Microscope,
  Settings,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import clsx from "clsx";
import { useUIStore } from "../stores/ui";

const NAV_ITEMS = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/accounts", label: "Accounts", icon: Wallet },
  { to: "/data", label: "Data", icon: Database },
  { to: "/workers", label: "Workers", icon: Server },
  { to: "/algorithms", label: "Algorithms", icon: Bot },
  { to: "/backtests", label: "Backtests", icon: FlaskConical },
  { to: "/research", label: "Research", icon: Microscope },
  { to: "/settings", label: "Settings", icon: Settings },
];

interface LayoutProps {
  children: React.ReactNode;
}

export function Layout({ children }: LayoutProps) {
  const { sidebarOpen, toggleSidebar } = useUIStore();

  return (
    <div className="flex h-screen overflow-hidden bg-gray-950 text-gray-100">
      {/* Sidebar */}
      <aside
        className={clsx(
          "flex flex-col bg-gray-900 border-r border-gray-800 transition-all duration-200",
          sidebarOpen ? "w-56" : "w-14"
        )}
      >
        {/* Logo / brand */}
        <div className="flex items-center justify-between px-3 py-4 border-b border-gray-800">
          {sidebarOpen && (
            <span className="text-sm font-bold tracking-wide text-white truncate">
              QuiltTrader
            </span>
          )}
          <button
            onClick={toggleSidebar}
            className="p-1 rounded hover:bg-gray-700 text-gray-400 hover:text-white ml-auto"
            aria-label="Toggle sidebar"
          >
            {sidebarOpen ? (
              <ChevronLeft size={16} />
            ) : (
              <ChevronRight size={16} />
            )}
          </button>
        </div>

        {/* Nav links */}
        <nav className="flex-1 py-2 overflow-y-auto">
          {NAV_ITEMS.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                clsx(
                  "flex items-center gap-3 px-3 py-2 mx-2 my-0.5 rounded text-sm font-medium transition-colors",
                  isActive
                    ? "bg-indigo-600 text-white"
                    : "text-gray-400 hover:bg-gray-700 hover:text-white"
                )
              }
            >
              <Icon size={18} className="shrink-0" />
              {sidebarOpen && <span className="truncate">{label}</span>}
            </NavLink>
          ))}
        </nav>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto p-6">{children}</main>
    </div>
  );
}
