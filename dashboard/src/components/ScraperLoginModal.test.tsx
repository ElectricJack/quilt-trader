import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  api,
  type ScraperLoginCommand,
  type ScraperLoginSession,
  type ScraperRecord,
} from "../api/client";
import {
  ScraperLoginModal,
  chunkText,
  formatTimeLeft,
  frameSizeOf,
  pointerToPage,
} from "./ScraperLoginModal";

// ─── Fakes ────────────────────────────────────────────────────────────────────

class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: FakeWebSocket[] = [];

  url: string;
  readyState = FakeWebSocket.CONNECTING;
  sent: ScraperLoginCommand[] = [];
  onopen: ((e: Event) => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onclose: ((e: CloseEvent) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(JSON.parse(data) as ScraperLoginCommand);
  }

  close() {
    if (this.readyState === FakeWebSocket.CLOSED) return;
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.({ code: 1000 } as CloseEvent);
  }

  // ── server side ──
  serverOpen() {
    act(() => {
      this.readyState = FakeWebSocket.OPEN;
      this.onopen?.(new Event("open"));
    });
  }

  serverSend(msg: object) {
    act(() => {
      this.onmessage?.({ data: JSON.stringify(msg) } as MessageEvent);
    });
  }

  serverClose(code: number) {
    act(() => {
      this.readyState = FakeWebSocket.CLOSED;
      this.onclose?.({ code } as CloseEvent);
    });
  }
}

function lastSocket(): FakeWebSocket {
  const ws = FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
  if (!ws) throw new Error("no websocket opened");
  return ws;
}

// Manual animation frames, so pointer-move coalescing is observable.
let rafQueue: Map<number, FrameRequestCallback>;
let rafNext: number;
function runAnimationFrame() {
  const callbacks = [...rafQueue.values()];
  rafQueue.clear();
  act(() => {
    for (const cb of callbacks) cb(performance.now());
  });
}

function session(overrides: Partial<ScraperLoginSession> = {}): ScraperLoginSession {
  return {
    id: "sess-1",
    state: "waiting_for_user",
    message: null,
    started_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 20 * 60_000).toISOString(),
    can_check: true,
    ...overrides,
  };
}

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
    last_error: null,
    auth: { kind: "browser_profile", login_supported: true, headed: true },
    auth_state: "needs_login",
    auth_reason: "auth_required",
    auth_message: null,
    auth_changed_at: null,
    schedule_paused: true,
    login_session: null,
    ...overrides,
  };
}

const FRAME = {
  type: "frame",
  data: "AAAA",
  metadata: { deviceWidth: 1280, deviceHeight: 720, pageScaleFactor: 1, offsetTop: 0 },
};

const PAGES = {
  type: "pages",
  pages: [
    { id: "p1", url: "https://seekingalpha.com/login", title: "Sign in", active: false },
    { id: "p2", url: "https://accounts.google.com/", title: "Sign in with Google", active: true },
  ],
};

let startSpy: ReturnType<typeof vi.spyOn>;
let getSpy: ReturnType<typeof vi.spyOn>;
let checkSpy: ReturnType<typeof vi.spyOn>;
let cancelSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
  rafQueue = new Map();
  rafNext = 1;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    const id = rafNext++;
    rafQueue.set(id, cb);
    return id;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => {
    rafQueue.delete(id);
  });
  startSpy = vi.spyOn(api, "startScraperLogin").mockResolvedValue(session());
  getSpy = vi.spyOn(api, "getScraperLogin").mockRejectedValue(new Error("404: no login session"));
  checkSpy = vi.spyOn(api, "checkScraperLogin").mockResolvedValue(session({ state: "checking" }));
  cancelSpy = vi.spyOn(api, "cancelScraperLogin").mockResolvedValue(session());
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function renderModal(props: { scraper?: ScraperRecord | null; onClose?: () => void } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onClose = props.onClose ?? vi.fn();
  const utils = render(
    <QueryClientProvider client={qc}>
      <ScraperLoginModal
        name="alpha-picks-scraper"
        scraper={props.scraper === undefined ? scraper() : props.scraper}
        onClose={onClose}
      />
    </QueryClientProvider>,
  );
  return { ...utils, onClose };
}

/** Open the modal, attach the socket and deliver the session, a frame and the pages. */
async function openViewer(s: ScraperLoginSession = session()) {
  const utils = renderModal();
  await waitFor(() => expect(FakeWebSocket.instances.length).toBe(1));
  const ws = lastSocket();
  ws.serverOpen();
  ws.serverSend({ type: "session", session: s });
  ws.serverSend(FRAME);
  ws.serverSend(PAGES);
  const overlay = await screen.findByTestId("login-overlay");
  vi.spyOn(overlay, "getBoundingClientRect").mockReturnValue({
    left: 0,
    top: 0,
    width: 640,
    height: 360,
    right: 640,
    bottom: 360,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  } as DOMRect);
  const keyboard = screen.getByTestId("login-keyboard") as HTMLTextAreaElement;
  return { ...utils, ws, overlay, keyboard };
}

// ─── Pure helpers ─────────────────────────────────────────────────────────────

describe("pointerToPage", () => {
  it("scales the offset in the image to the page's CSS pixels", () => {
    const rect = { left: 10, top: 20, width: 640, height: 360 };
    expect(pointerToPage(330, 200, rect, { width: 1280, height: 720 })).toEqual({ x: 640, y: 360 });
    expect(pointerToPage(10, 20, rect, { width: 1280, height: 720 })).toEqual({ x: 0, y: 0 });
    // A phone-sized image of the same page.
    expect(pointerToPage(100, 50, { left: 0, top: 0, width: 400, height: 225 }, { width: 1600, height: 900 }))
      .toEqual({ x: 400, y: 200 });
  });

  it("clamps positions outside the image to its edge", () => {
    const rect = { left: 0, top: 0, width: 640, height: 360 };
    expect(pointerToPage(-50, 999, rect, { width: 1280, height: 720 })).toEqual({ x: 0, y: 720 });
  });

  it("returns null before the image has a size", () => {
    expect(pointerToPage(5, 5, { left: 0, top: 0, width: 0, height: 0 }, { width: 1280, height: 720 })).toBeNull();
  });

  it("takes the page size from the frame metadata, then the image", () => {
    expect(frameSizeOf({ data: "", metadata: { deviceWidth: 1024, deviceHeight: 768 } })).toEqual({
      width: 1024,
      height: 768,
    });
    expect(frameSizeOf({ data: "", metadata: {} }, { width: 800, height: 600 })).toEqual({
      width: 800,
      height: 600,
    });
    expect(frameSizeOf(null)).toEqual({ width: 1280, height: 720 });
  });
});

describe("chunkText / formatTimeLeft", () => {
  it("splits text into ≤ 256-character commands without breaking a code point", () => {
    const chunks = chunkText("a".repeat(255) + "😀" + "b".repeat(10));
    expect(chunks).toHaveLength(2);
    expect(chunks[0]).toBe("a".repeat(255) + "😀");
    expect(chunks[1]).toBe("b".repeat(10));
    expect(chunkText("")).toEqual([]);
  });

  it("formats the time left as m:ss", () => {
    expect(formatTimeLeft(20 * 60_000)).toBe("20:00");
    expect(formatTimeLeft(65_500)).toBe("1:05");
    expect(formatTimeLeft(-10)).toBe("0:00");
  });
});

// ─── Session start and viewer ─────────────────────────────────────────────────

describe("ScraperLoginModal", () => {
  it("starts a session and attaches the viewer socket to it", async () => {
    const { ws } = await openViewer();
    expect(startSpy).toHaveBeenCalledWith("alpha-picks-scraper");
    expect(ws.url).toMatch(/\/ws\/scrapers\/alpha-picks-scraper\/login\?session=sess-1$/);
    const img = screen.getByAltText("The login browser");
    expect(img).toHaveAttribute("src", "data:image/jpeg;base64,AAAA");
    expect(screen.getByTestId("login-state")).toHaveTextContent("Waiting for you");
    expect(screen.getByText(/left$/)).toBeInTheDocument();
    expect(screen.getByText("A browser window is also open on the coordinator's display.")).toBeInTheDocument();
  });

  it("reattaches to the scraper's active session instead of starting one", async () => {
    renderModal({ scraper: scraper({ login_session: session({ id: "existing" }) }) });
    await waitFor(() => expect(FakeWebSocket.instances.length).toBe(1));
    expect(lastSocket().url).toMatch(/session=existing$/);
    expect(startSpy).not.toHaveBeenCalled();
  });

  it("hides the headed note for a headless scraper", async () => {
    renderModal({ scraper: scraper({ auth: { kind: "browser_profile", login_supported: true, headed: false } }) });
    await waitFor(() => expect(FakeWebSocket.instances.length).toBe(1));
    lastSocket().serverOpen();
    lastSocket().serverSend({ type: "session", session: session() });
    expect(screen.queryByText(/A browser window is also open/)).not.toBeInTheDocument();
  });

  // ── pointer ──

  it("maps clicks on the overlay to page coordinates from the frame metadata", async () => {
    const { ws, overlay } = await openViewer();
    fireEvent.pointerDown(overlay, { clientX: 100, clientY: 50, button: 0, pointerId: 1 });
    fireEvent.pointerUp(overlay, { clientX: 100, clientY: 50, button: 0, pointerId: 1 });
    expect(ws.sent).toEqual([
      { cmd: "mouse", action: "down", x: 200, y: 100, button: "left", click_count: 1 },
      { cmd: "mouse", action: "up", x: 200, y: 100, button: "left", click_count: 1 },
    ]);
  });

  it("counts a quick second click as a double click", async () => {
    const { ws, overlay } = await openViewer();
    for (let i = 0; i < 2; i++) {
      fireEvent.pointerDown(overlay, { clientX: 320, clientY: 180, button: 0, pointerId: 1 });
      fireEvent.pointerUp(overlay, { clientX: 320, clientY: 180, button: 0, pointerId: 1 });
    }
    const downs = ws.sent.filter((c) => c.cmd === "mouse" && c.action === "down");
    expect(downs.map((c) => (c as { click_count: number }).click_count)).toEqual([1, 2]);
    expect(downs[1]).toMatchObject({ x: 640, y: 360 });
  });

  it("forwards the right button and suppresses the local context menu", async () => {
    const { ws, overlay } = await openViewer();
    fireEvent.pointerDown(overlay, { clientX: 64, clientY: 36, button: 2, pointerId: 1 });
    expect(fireEvent.contextMenu(overlay)).toBe(false);
    fireEvent.pointerUp(overlay, { clientX: 64, clientY: 36, button: 2, pointerId: 1 });
    expect(ws.sent).toEqual([
      { cmd: "mouse", action: "down", x: 128, y: 72, button: "right", click_count: 1 },
      { cmd: "mouse", action: "up", x: 128, y: 72, button: "right", click_count: 1 },
    ]);
  });

  it("coalesces pointer moves to one per animation frame", async () => {
    const { ws, overlay } = await openViewer();
    fireEvent.pointerMove(overlay, { clientX: 10, clientY: 10, pointerId: 1 });
    fireEvent.pointerMove(overlay, { clientX: 20, clientY: 20, pointerId: 1 });
    fireEvent.pointerMove(overlay, { clientX: 32, clientY: 18, pointerId: 1 });
    expect(ws.sent).toEqual([]);
    runAnimationFrame();
    expect(ws.sent).toEqual([{ cmd: "mouse", action: "move", x: 64, y: 36 }]);
  });

  it("sends a pending move before the button goes down", async () => {
    const { ws, overlay } = await openViewer();
    fireEvent.pointerMove(overlay, { clientX: 32, clientY: 18, pointerId: 1 });
    fireEvent.pointerDown(overlay, { clientX: 32, clientY: 18, button: 0, pointerId: 1 });
    expect(ws.sent.map((c) => (c as { action?: string }).action)).toEqual(["move", "down"]);
    runAnimationFrame();
    expect(ws.sent).toHaveLength(2);
  });

  it("forwards the wheel in page pixels", async () => {
    const { ws, overlay } = await openViewer();
    fireEvent.wheel(overlay, { clientX: 320, clientY: 180, deltaX: 0, deltaY: 100, deltaMode: 0 });
    fireEvent.wheel(overlay, { clientX: 320, clientY: 180, deltaX: 0, deltaY: 3, deltaMode: 1 });
    runAnimationFrame();
    expect(ws.sent).toEqual([{ cmd: "wheel", x: 640, y: 360, dx: 0, dy: 148 }]);
  });

  // ── keyboard ──

  it("forwards keydown and keyup with key and code", async () => {
    const { ws, keyboard } = await openViewer();
    expect(fireEvent.keyDown(keyboard, { key: "a", code: "KeyA" })).toBe(false);
    fireEvent.keyUp(keyboard, { key: "a", code: "KeyA" });
    expect(ws.sent).toEqual([
      { cmd: "key", action: "down", key: "a", code: "KeyA" },
      { cmd: "key", action: "up", key: "a", code: "KeyA" },
    ]);
  });

  it("keeps Tab and Backspace in the remote page", async () => {
    const { ws, keyboard } = await openViewer();
    expect(fireEvent.keyDown(keyboard, { key: "Tab", code: "Tab" })).toBe(false);
    expect(fireEvent.keyDown(keyboard, { key: "Backspace", code: "Backspace" })).toBe(false);
    expect(ws.sent.map((c) => (c as { key: string }).key)).toEqual(["Tab", "Backspace"]);
  });

  it("sends a paste as text, not as a remote Ctrl+V", async () => {
    const { ws, keyboard } = await openViewer();
    fireEvent.keyDown(keyboard, { key: "Control", code: "ControlLeft", ctrlKey: true });
    // Ctrl+V is left to the browser so that the paste event fires.
    expect(fireEvent.keyDown(keyboard, { key: "v", code: "KeyV", ctrlKey: true })).toBe(true);
    fireEvent.paste(keyboard, {
      clipboardData: { getData: (type: string) => (type === "text/plain" ? "hunter2" : "") },
    });
    fireEvent.keyUp(keyboard, { key: "v", code: "KeyV", ctrlKey: true });
    fireEvent.keyUp(keyboard, { key: "Control", code: "ControlLeft" });
    expect(ws.sent).toEqual([
      { cmd: "key", action: "down", key: "Control", code: "ControlLeft" },
      { cmd: "text", text: "hunter2" },
      { cmd: "key", action: "up", key: "Control", code: "ControlLeft" },
    ]);
  });

  it("splits a long paste into several text commands", async () => {
    const { ws, keyboard } = await openViewer();
    fireEvent.paste(keyboard, { clipboardData: { getData: () => "x".repeat(300) } });
    expect(ws.sent).toEqual([
      { cmd: "text", text: "x".repeat(256) },
      { cmd: "text", text: "x".repeat(44) },
    ]);
  });

  it("releases every held key when the keyboard loses focus", async () => {
    const { ws, keyboard } = await openViewer();
    act(() => keyboard.focus());
    fireEvent.keyDown(keyboard, { key: "Shift", code: "ShiftLeft", shiftKey: true });
    fireEvent.keyDown(keyboard, { key: "A", code: "KeyA", shiftKey: true });
    ws.sent = [];
    act(() => keyboard.blur());
    expect(ws.sent).toEqual([
      { cmd: "key", action: "up", key: "Shift", code: "ShiftLeft" },
      { cmd: "key", action: "up", key: "A", code: "KeyA" },
    ]);
    // Nothing is left to release a second time.
    ws.sent = [];
    fireEvent.keyUp(keyboard, { key: "A", code: "KeyA" });
    expect(ws.sent).toEqual([]);
  });

  it("releases keys held with Cmd when Cmd goes up (macOS sends no keyup for them)", async () => {
    const { ws, keyboard } = await openViewer();
    fireEvent.keyDown(keyboard, { key: "Meta", code: "MetaLeft", metaKey: true });
    fireEvent.keyDown(keyboard, { key: "a", code: "KeyA", metaKey: true });
    ws.sent = [];
    fireEvent.keyUp(keyboard, { key: "Meta", code: "MetaLeft" });
    expect(ws.sent).toEqual([
      { cmd: "key", action: "up", key: "a", code: "KeyA" },
      { cmd: "key", action: "up", key: "Meta", code: "MetaLeft" },
    ]);
  });

  it("leaves IME and soft-keyboard keys to the input event, which sends text", async () => {
    const { ws, keyboard } = await openViewer();
    expect(fireEvent.keyDown(keyboard, { key: "Unidentified", keyCode: 229 })).toBe(true);
    keyboard.value = "abc";
    fireEvent.input(keyboard);
    expect(keyboard.value).toBe("");
    expect(ws.sent).toEqual([{ cmd: "text", text: "abc" }]);
  });

  it("releases held keys when the viewer closes because the session moved on", async () => {
    const { ws, keyboard } = await openViewer();
    fireEvent.keyDown(keyboard, { key: "Enter", code: "Enter" });
    ws.sent = [];
    ws.serverSend({ type: "session", session: session({ state: "verified" }) });
    expect(screen.queryByTestId("login-keyboard")).not.toBeInTheDocument();
    expect(ws.sent).toEqual([{ cmd: "key", action: "up", key: "Enter", code: "Enter" }]);
  });

  it("the Keyboard button focuses the capture field with a soft keyboard", async () => {
    const { keyboard } = await openViewer();
    expect(keyboard).toHaveAttribute("inputmode", "none");
    act(() => keyboard.blur());
    fireEvent.click(screen.getByRole("button", { name: "Keyboard" }));
    expect(keyboard).toHaveAttribute("inputmode", "text");
    expect(document.activeElement).toBe(keyboard);
  });

  it("clicking the page moves keyboard focus into the viewer", async () => {
    const { overlay, keyboard } = await openViewer();
    act(() => keyboard.blur());
    expect(document.activeElement).not.toBe(keyboard);
    fireEvent.pointerDown(overlay, { clientX: 1, clientY: 1, button: 0, pointerId: 1 });
    expect(document.activeElement).toBe(keyboard);
  });

  // ── toolbar ──

  it("sends history, navigation and tab commands from the toolbar", async () => {
    const { ws } = await openViewer();
    fireEvent.click(screen.getByRole("button", { name: /Back/ }));
    fireEvent.click(screen.getByRole("button", { name: /Reload/ }));
    fireEvent.click(screen.getByRole("button", { name: /Go to sign-in page/ }));
    const tabs = screen.getByRole("combobox", { name: "Browser tab" }) as HTMLSelectElement;
    expect(tabs.value).toBe("p2");
    fireEvent.change(tabs, { target: { value: "p1" } });
    expect(ws.sent).toEqual([
      { cmd: "history", action: "back" },
      { cmd: "history", action: "reload" },
      { cmd: "navigate", target: "login" },
      { cmd: "switch_page", id: "p1" },
    ]);
  });

  it("Check now and Cancel call the login routes", async () => {
    await openViewer();
    fireEvent.click(screen.getByRole("button", { name: /Check now/ }));
    await waitFor(() => expect(checkSpy).toHaveBeenCalledWith("alpha-picks-scraper"));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(cancelSpy).toHaveBeenCalledWith("alpha-picks-scraper"));
    expect(screen.getByRole("button", { name: "Cancelling…" })).toBeDisabled();
  });

  it("disables Check now without a verify URL", async () => {
    await openViewer(session({ can_check: false }));
    expect(screen.getByRole("button", { name: /Check now/ })).toBeDisabled();
  });

  it("shows the helper's status message", async () => {
    const { ws } = await openViewer();
    ws.serverSend({
      type: "session",
      session: session({ message: "Not signed in yet — finish signing in, then Check now." }),
    });
    expect(screen.getByText("Not signed in yet — finish signing in, then Check now.")).toBeInTheDocument();
  });

  // ── end states ──

  it("shows a spinner while the confirmation scrape runs", async () => {
    const { ws } = await openViewer();
    ws.serverSend({
      type: "session",
      session: session({ state: "confirming", message: "Running one scrape to confirm the sign-in…" }),
    });
    expect(screen.getByTestId("login-state")).toHaveTextContent("Confirming");
    expect(screen.getByText("Running one scrape to confirm the sign-in…")).toBeInTheDocument();
    expect(screen.queryByTestId("login-overlay")).not.toBeInTheDocument();
  });

  it("succeeded shows the row count and only Close", async () => {
    const { ws, onClose } = await openViewer();
    ws.serverSend({
      type: "ended",
      session: session({ state: "succeeded", message: "Signed in and scraped 12 rows." }),
    });
    expect(screen.getByTestId("login-end-message")).toHaveTextContent("Signed in and scraped 12 rows.");
    expect(screen.queryByRole("button", { name: "Start again" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalled();
  });

  it.each([
    ["confirm_failed", "The confirmation scrape failed: ParseError: no table"],
    ["failed", "patchright is not installed in the scraper's venv"],
    ["timed_out", "The login session reached its time limit. Start again to retry."],
  ] as const)("%s shows the message and Start again", async (state, message) => {
    const { ws } = await openViewer();
    ws.serverSend({ type: "ended", session: session({ state, message }) });
    expect(screen.getByTestId("login-end-message")).toHaveTextContent(message);
    // The socket is done: no reconnect after the server closes it.
    ws.serverClose(1000);
    expect(FakeWebSocket.instances).toHaveLength(1);

    startSpy.mockResolvedValue(session({ id: "sess-2" }));
    fireEvent.click(screen.getByRole("button", { name: "Start again" }));
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(2));
    expect(lastSocket().url).toMatch(/session=sess-2$/);
    expect(startSpy).toHaveBeenCalledTimes(2);
  });

  it("recovers the final state when the session ended before the viewer attached", async () => {
    getSpy.mockResolvedValue(
      session({ state: "failed", message: "patchright is not installed in the scraper's venv" }),
    );
    renderModal();
    await waitFor(() => expect(FakeWebSocket.instances.length).toBe(1));
    lastSocket().serverClose(4404);
    expect(await screen.findByTestId("login-end-message")).toHaveTextContent(
      "patchright is not installed in the scraper's venv",
    );
    expect(screen.getByRole("button", { name: "Start again" })).toBeInTheDocument();
  });

  it("says the session is gone when the coordinator no longer knows it", async () => {
    renderModal();
    await waitFor(() => expect(FakeWebSocket.instances.length).toBe(1));
    lastSocket().serverClose(4404);
    expect(await screen.findByTestId("login-end-message")).toHaveTextContent(
      /no longer running/,
    );
  });

  it("reconnects after an unexpected disconnect while the session is active", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { ws } = await openViewer();
      ws.serverClose(1006);
      expect(screen.getByText("Reconnecting…")).toBeInTheDocument();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1_100);
      });
      expect(FakeWebSocket.instances).toHaveLength(2);
      expect(lastSocket().url).toMatch(/session=sess-1$/);
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows why a session couldn't start and offers to try again", async () => {
    startSpy.mockRejectedValue(new Error("409: a scrape is running; try again when it finishes"));
    renderModal();
    expect(await screen.findByTestId("login-end-message")).toHaveTextContent(
      "Couldn't open a login session: a scrape is running; try again when it finishes",
    );
    startSpy.mockResolvedValue(session());
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
  });
});
