import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { ScraperRecord } from "../api/client";

const scrapersRef: { data: ScraperRecord[] | undefined } = { data: [] };

vi.mock("../api/hooks", () => ({
  useScrapers: () => ({ data: scrapersRef.data, isLoading: false }),
}));

import { ScraperAuthBanner, formatAuthTime, parseUtc, scraperAuthBannerText } from "./ScraperAuthBanner";
import { useUIStore } from "../stores/ui";

function scraper(overrides: Partial<ScraperRecord> = {}): ScraperRecord {
  return {
    name: "alpha-picks-scraper",
    schedule: "0 16 * * 1-5",
    jitter_seconds: null,
    next_run_at: null,
    version: "1.0.0",
    description: null,
    config_overrides: [],
    last_status: "failed",
    last_run_at: null,
    data_url: "/api/data/custom/alpha-picks-scraper",
    last_error: "login wall",
    auth: { kind: "browser_profile", login_supported: true, headed: true },
    auth_state: "needs_login",
    auth_reason: "auth_required",
    auth_message: "Seeking Alpha showed a login wall",
    auth_changed_at: null,
    schedule_paused: true,
    login_session: null,
    ...overrides,
  };
}

// A local 14:23 today, as the UTC ISO string the coordinator would send.
function localIso(hours: number, minutes: number, dayOffset = 0): string {
  const d = new Date();
  d.setDate(d.getDate() + dayOffset);
  d.setHours(hours, minutes, 0, 0);
  return d.toISOString();
}

function renderBanner() {
  return render(
    <MemoryRouter>
      <ScraperAuthBanner />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  scrapersRef.data = [];
  useUIStore.setState({ scraperLoginName: null });
});

describe("ScraperAuthBanner", () => {
  it("renders nothing while every scraper is ok", () => {
    scrapersRef.data = [scraper({ auth_state: "ok", auth_reason: null, schedule_paused: false })];
    const { container } = renderBanner();
    expect(container).toBeEmptyDOMElement();
  });

  it("explains a login wall with the time it happened", () => {
    scrapersRef.data = [scraper({ auth_reason: "auth_required", auth_changed_at: localIso(14, 23) })];
    renderBanner();
    expect(
      screen.getByText(
        "alpha-picks-scraper needs you to sign in again: the site showed a login wall at 14:23. " +
          "Scheduled runs are paused.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Re-login" })).toBeInTheDocument();
  });

  it("explains a bot-check block and that signing in usually clears it", () => {
    scrapersRef.data = [scraper({ auth_reason: "bot_blocked", auth_changed_at: localIso(9, 5) })];
    renderBanner();
    expect(
      screen.getByText(
        "alpha-picks-scraper needs you to sign in again: the site's bot check blocked the browser. " +
          "Signing in again in the browser usually clears it. Scheduled runs are paused.",
      ),
    ).toBeInTheDocument();
  });

  it("shows one row per scraper that needs a login", () => {
    scrapersRef.data = [
      scraper({ name: "a-scraper" }),
      scraper({ name: "b-scraper", auth_state: "ok", auth_reason: null }),
      scraper({ name: "c-scraper", auth_reason: "bot_blocked" }),
    ];
    renderBanner();
    expect(screen.getByTestId("scraper-auth-banner-a-scraper")).toBeInTheDocument();
    expect(screen.queryByTestId("scraper-auth-banner-b-scraper")).not.toBeInTheDocument();
    expect(screen.getByTestId("scraper-auth-banner-c-scraper")).toBeInTheDocument();
  });

  it("Re-login opens the login modal for that scraper", () => {
    scrapersRef.data = [scraper()];
    renderBanner();
    fireEvent.click(screen.getByRole("button", { name: "Re-login" }));
    expect(useUIStore.getState().scraperLoginName).toBe("alpha-picks-scraper");
  });

  it("reads 'Login open…' and reopens the modal while a session is active", () => {
    scrapersRef.data = [
      scraper({
        login_session: {
          id: "s1",
          state: "waiting_for_user",
          message: null,
          started_at: null,
          expires_at: null,
          can_check: true,
        },
      }),
    ];
    renderBanner();
    fireEvent.click(screen.getByRole("button", { name: "Login open…" }));
    expect(useUIStore.getState().scraperLoginName).toBe("alpha-picks-scraper");
  });

  it("goes back to Re-login once the session has ended", () => {
    scrapersRef.data = [
      scraper({
        login_session: {
          id: "s1",
          state: "confirm_failed",
          message: "still a login wall",
          started_at: null,
          expires_at: null,
          can_check: true,
        },
      }),
    ];
    renderBanner();
    expect(screen.getByRole("button", { name: "Re-login" })).toBeInTheDocument();
  });

  it("without an auth block, offers the Data page and asks to fix the credentials", () => {
    scrapersRef.data = [scraper({ name: "api-scraper", auth: null, auth_reason: "auth_required" })];
    renderBanner();
    expect(screen.queryByRole("button", { name: "Re-login" })).not.toBeInTheDocument();
    const link = screen.getByRole("link", { name: "Open Data page" });
    expect(link).toHaveAttribute("href", "/data?tab=acquisition");
    expect(screen.getByText(/Scheduled runs are paused\. Fix the credentials, then Run now\.$/)).toBeInTheDocument();
  });
});

describe("scraperAuthBannerText", () => {
  it("drops the browser hint for a bot block on a scraper that can't open a login", () => {
    const text = scraperAuthBannerText(scraper({ auth: null, auth_reason: "bot_blocked" }));
    expect(text).toBe(
      "alpha-picks-scraper needs you to sign in again: the site's bot check blocked the browser. " +
        "Scheduled runs are paused. Fix the credentials, then Run now.",
    );
  });

  it("has a generic sentence when the reason is unknown", () => {
    expect(scraperAuthBannerText(scraper({ auth_reason: null }))).toBe(
      "alpha-picks-scraper needs you to sign in again. Scheduled runs are paused.",
    );
  });

  it("omits the time when the change time is missing", () => {
    expect(scraperAuthBannerText(scraper({ auth_changed_at: null }))).toBe(
      "alpha-picks-scraper needs you to sign in again: the site showed a login wall. " +
        "Scheduled runs are paused.",
    );
  });
});

describe("formatAuthTime", () => {
  it("shows only the time for today, and the date for another day", () => {
    expect(formatAuthTime(localIso(14, 23))).toBe("14:23");
    expect(formatAuthTime(localIso(8, 7, -1))).toMatch(/, 08:07$/);
  });

  it("reads a timestamp without a zone as UTC", () => {
    expect(parseUtc("2026-09-28T14:23:00")?.toISOString()).toBe("2026-09-28T14:23:00.000Z");
    expect(parseUtc("2026-09-28T14:23:00+00:00")?.toISOString()).toBe("2026-09-28T14:23:00.000Z");
    expect(parseUtc("not a date")).toBeNull();
    expect(formatAuthTime(null)).toBeNull();
  });
});
