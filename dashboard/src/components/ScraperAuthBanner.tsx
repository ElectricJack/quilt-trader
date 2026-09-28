import { Link } from "react-router-dom";
import { AlertTriangle, KeyRound } from "lucide-react";
import { useScrapers } from "../api/hooks";
import { isLoginSessionActive, type ScraperRecord } from "../api/client";
import { useUIStore } from "../stores/ui";

/** Parse an API timestamp; the coordinator sends UTC, so a bare one is read as UTC. */
export function parseUtc(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const hasZone = /(Z|[+-]\d{2}:?\d{2})$/.test(iso);
  const d = new Date(hasZone ? iso : `${iso}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "14:23" today, "Sep 27, 14:23" on another day, in the viewer's time zone. */
export function formatAuthTime(iso: string | null | undefined, now: Date = new Date()): string | null {
  const d = parseUtc(iso);
  if (!d) return null;
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  if (d.toDateString() === now.toDateString()) return time;
  return `${d.toLocaleDateString([], { month: "short", day: "numeric" })}, ${time}`;
}

/** The banner sentence for a scraper in needs_login (review rev-nimble-bridge section 10). */
export function scraperAuthBannerText(scraper: ScraperRecord, now: Date = new Date()): string {
  const canLogin = !!scraper.auth?.login_supported;
  const lead = `${scraper.name} needs you to sign in again`;
  let what: string;
  if (scraper.auth_reason === "auth_required") {
    const at = formatAuthTime(scraper.auth_changed_at, now);
    what = `${lead}: the site showed a login wall${at ? ` at ${at}` : ""}.`;
  } else if (scraper.auth_reason === "bot_blocked") {
    what = `${lead}: the site's bot check blocked the browser.`;
    if (canLogin) what += " Signing in again in the browser usually clears it.";
  } else {
    what = `${lead}.`;
  }
  const next = canLogin ? "" : " Fix the credentials, then Run now.";
  return `${what} Scheduled runs are paused.${next}`;
}

const BUTTON =
  "inline-flex items-center gap-1.5 shrink-0 px-2.5 py-1 rounded text-xs font-medium " +
  "text-amber-950 bg-amber-400 hover:bg-amber-300 transition-colors";

/** One amber row per scraper whose site wants a new sign-in; shown on every page. */
export function ScraperAuthBanner() {
  const { data: scrapers } = useScrapers();
  const openScraperLogin = useUIStore((s) => s.openScraperLogin);
  const needing = (scrapers ?? []).filter((s) => s.auth_state === "needs_login");
  if (needing.length === 0) return null;

  return (
    <div className="space-y-2 mb-4" role="region" aria-label="Scrapers that need a sign-in">
      {needing.map((s) => (
        <div
          key={s.name}
          data-testid={`scraper-auth-banner-${s.name}`}
          className="flex flex-wrap sm:flex-nowrap items-center gap-3 rounded border border-amber-700 bg-amber-900/30 px-3 py-2 text-sm text-amber-100"
        >
          <AlertTriangle size={16} className="shrink-0 text-amber-400" aria-hidden="true" />
          <p className="flex-1 min-w-0" title={s.auth_message ?? undefined}>
            {scraperAuthBannerText(s)}
          </p>
          {s.auth?.login_supported ? (
            <button type="button" onClick={() => openScraperLogin(s.name)} className={BUTTON}>
              <KeyRound size={13} aria-hidden="true" />
              {isLoginSessionActive(s.login_session) ? "Login open…" : "Re-login"}
            </button>
          ) : (
            <Link to="/data?tab=acquisition" className={BUTTON}>
              Open Data page
            </Link>
          )}
        </div>
      ))}
    </div>
  );
}
