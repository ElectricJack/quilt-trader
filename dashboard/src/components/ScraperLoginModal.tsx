import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Keyboard,
  Loader2,
  LogIn,
  RotateCw,
  ShieldCheck,
  X,
} from "lucide-react";
import clsx from "clsx";
import {
  api,
  isLoginSessionActive,
  SCRAPER_LOGIN_MAX_TEXT_CHARS,
  type ScraperLoginCommand,
  type ScraperLoginFrame,
  type ScraperLoginMouseButton,
  type ScraperLoginPage,
  type ScraperLoginSession,
  type ScraperLoginState,
  type ScraperRecord,
} from "../api/client";
import {
  keys,
  useCancelScraperLogin,
  useCheckScraperLogin,
  useScrapers,
  useScraperLoginSocket,
  useStartScraperLogin,
} from "../api/hooks";
import { useUIStore } from "../stores/ui";
import { parseUtc } from "./ScraperAuthBanner";

// ─── Pure helpers (exported for tests) ────────────────────────────────────────

export interface FrameSize {
  width: number;
  height: number;
}

/** Viewport the helper launches with when no frame has said otherwise. */
const DEFAULT_FRAME_SIZE: FrameSize = { width: 1280, height: 720 };
const DOUBLE_CLICK_MS = 500;
const DOUBLE_CLICK_SLOP_PX = 4;
const MAX_CLICK_COUNT = 3;
const MAX_WHEEL_DELTA = 10_000;
const WHEEL_LINE_PX = 16;

/**
 * Map a pointer position on the viewer to CSS px in the remote page:
 * x = (offsetX / imgClientWidth) × deviceWidth, and likewise for y.
 * Clamped to the page, so a drag that leaves the image stays on its edge.
 */
export function pointerToPage(
  clientX: number,
  clientY: number,
  rect: { left: number; top: number; width: number; height: number },
  frame: FrameSize,
): { x: number; y: number } | null {
  if (!(rect.width > 0) || !(rect.height > 0) || !(frame.width > 0) || !(frame.height > 0)) {
    return null;
  }
  const x = ((clientX - rect.left) / rect.width) * frame.width;
  const y = ((clientY - rect.top) / rect.height) * frame.height;
  const round = (v: number) => Math.round(v * 100) / 100;
  return {
    x: round(Math.min(Math.max(x, 0), frame.width)),
    y: round(Math.min(Math.max(y, 0), frame.height)),
  };
}

/** The page size a frame describes: its metadata, else the image, else the default viewport. */
export function frameSizeOf(
  frame: ScraperLoginFrame | null,
  natural?: { width: number; height: number } | null,
): FrameSize {
  const w = frame?.metadata.deviceWidth;
  const h = frame?.metadata.deviceHeight;
  if (typeof w === "number" && typeof h === "number" && w > 0 && h > 0) {
    return { width: w, height: h };
  }
  if (natural && natural.width > 0 && natural.height > 0) return natural;
  return DEFAULT_FRAME_SIZE;
}

export function mouseButtonOf(button: number): ScraperLoginMouseButton | null {
  if (button === 0) return "left";
  if (button === 1) return "middle";
  if (button === 2) return "right";
  return null;
}

/** Split text into commands the helper accepts (≤ 256 characters, never splitting a code point). */
export function chunkText(text: string, max: number = SCRAPER_LOGIN_MAX_TEXT_CHARS): string[] {
  const chars = Array.from(text);
  const out: string[] = [];
  for (let i = 0; i < chars.length; i += max) out.push(chars.slice(i, i + max).join(""));
  return out;
}

function wheelPixels(e: WheelEvent, frame: FrameSize): { dx: number; dy: number } {
  const scale =
    e.deltaMode === 1 ? WHEEL_LINE_PX : e.deltaMode === 2 ? frame.height : 1;
  return { dx: e.deltaX * scale, dy: e.deltaY * scale };
}

function clampWheel(v: number): number {
  return Math.min(Math.max(v, -MAX_WHEEL_DELTA), MAX_WHEEL_DELTA);
}

export function formatTimeLeft(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function errorText(e: unknown): string {
  const message = e instanceof Error ? e.message : String(e);
  return message.replace(/^\d{3}: /, "");
}

const STATE_LABELS: Record<ScraperLoginState, string> = {
  starting: "Starting",
  waiting_for_user: "Waiting for you",
  checking: "Checking",
  verified: "Verified",
  browser_closed: "Browser closed",
  confirming: "Confirming",
  succeeded: "Signed in",
  confirm_failed: "Confirmation failed",
  cancelled: "Cancelled",
  timed_out: "Timed out",
  failed: "Failed",
};

const STATE_CLASSES: Partial<Record<ScraperLoginState, string>> = {
  waiting_for_user: "bg-blue-900/40 text-blue-300 border-blue-800",
  checking: "bg-blue-900/40 text-blue-300 border-blue-800",
  verified: "bg-green-900/40 text-green-300 border-green-800",
  confirming: "bg-green-900/40 text-green-300 border-green-800",
  succeeded: "bg-green-900/40 text-green-300 border-green-800",
  confirm_failed: "bg-red-900/40 text-red-300 border-red-800",
  failed: "bg-red-900/40 text-red-300 border-red-800",
  timed_out: "bg-amber-900/40 text-amber-300 border-amber-800",
};

const END_MESSAGES: Partial<Record<ScraperLoginState, string>> = {
  succeeded: "Signed in, and the confirmation scrape succeeded.",
  confirm_failed: "The confirmation scrape failed.",
  cancelled: "The login session was cancelled.",
  timed_out: "The login session reached its time limit. Start again to retry.",
  failed: "The login session failed.",
};

/** States after the person's part: the browser is closing or closed, a scrape confirms. */
const CONFIRMING_STATES: ReadonlySet<ScraperLoginState> = new Set([
  "verified",
  "browser_closed",
  "confirming",
]);

const TOOL_BUTTON =
  "inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded text-xs font-medium text-gray-200 " +
  "bg-gray-800 hover:bg-gray-700 border border-gray-700 transition-colors " +
  "disabled:opacity-50 disabled:cursor-not-allowed";

// ─── Host: the one modal instance, opened from the banner or the Data page ────

export function ScraperLoginHost() {
  const name = useUIStore((s) => s.scraperLoginName);
  const close = useUIStore((s) => s.closeScraperLogin);
  const { data: scrapers } = useScrapers();
  if (!name) return null;
  const scraper = scrapers?.find((s) => s.name === name) ?? null;
  return <ScraperLoginModal key={name} name={name} scraper={scraper} onClose={close} />;
}

// ─── Modal ────────────────────────────────────────────────────────────────────

interface ScraperLoginModalProps {
  name: string;
  /** The scraper's record, for `auth.headed` and an active session to reattach to. */
  scraper: ScraperRecord | null;
  /** Hide the modal. An active session keeps running; Re-login reattaches. */
  onClose: () => void;
}

export function ScraperLoginModal({ name, scraper, onClose }: ScraperLoginModalProps) {
  const qc = useQueryClient();
  const { mutateAsync: startLogin, isPending: starting } = useStartScraperLogin();
  const { mutateAsync: checkLogin, isPending: checking } = useCheckScraperLogin();
  const { mutateAsync: cancelLogin } = useCancelScraperLogin();

  const [seed, setSeed] = useState<ScraperLoginSession | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  // The final session, fetched when the socket says the session is gone (4404).
  const [recovered, setRecovered] = useState<ScraperLoginSession | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [cancelRequested, setCancelRequested] = useState(false);

  const begin = useCallback(async () => {
    setStartError(null);
    setSeed(null);
    setRecovered(null);
    setActionError(null);
    setCancelRequested(false);
    try {
      setSeed(await startLogin(name));
    } catch (e) {
      setStartError(errorText(e));
    }
  }, [name, startLogin]);

  // On open, reattach to the scraper's active session; otherwise start one.
  // (POST also returns an active session with 200, so a stale list is harmless.)
  const initialized = useRef(false);
  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    const existing = scraper?.login_session;
    if (existing && isLoginSessionActive(existing)) setSeed(existing);
    else void begin();
  }, [begin, scraper]);

  const socket = useScraperLoginSocket(name, seed?.id ?? null);
  const session = recovered ?? socket.session ?? seed;
  const active = isLoginSessionActive(session);
  const ended = session != null && !active;

  // The session ended before this viewer attached, or the coordinator restarted.
  useEffect(() => {
    if (!socket.gone || !seed || socket.ended) return;
    let cancelled = false;
    const lost: ScraperLoginSession = {
      ...seed,
      state: "failed",
      message: "This login session is no longer running (the coordinator may have restarted).",
    };
    api
      .getScraperLogin(name)
      .then((s) => {
        if (!cancelled) setRecovered(s.id === seed.id && !isLoginSessionActive(s) ? s : lost);
      })
      .catch(() => {
        if (!cancelled) setRecovered(lost);
      });
    return () => {
      cancelled = true;
    };
  }, [socket.gone, socket.ended, seed, name]);

  // A finished session changed auth state and maybe the scraper's data.
  useEffect(() => {
    if (!ended) return;
    void qc.invalidateQueries({ queryKey: keys.scrapers() });
    void qc.invalidateQueries({ queryKey: ["data-sources"] });
  }, [ended, qc]);

  const now = useNow(active);
  const expiresAt = parseUtc(session?.expires_at);
  const timeLeft = active && expiresAt ? formatTimeLeft(expiresAt.getTime() - now) : null;

  async function handleCheck() {
    setActionError(null);
    try {
      await checkLogin(name);
    } catch (e) {
      setActionError(errorText(e));
    }
  }

  async function handleCancel() {
    setActionError(null);
    setCancelRequested(true);
    try {
      await cancelLogin(name);
    } catch (e) {
      setCancelRequested(false);
      setActionError(errorText(e));
    }
  }

  const state = session?.state ?? null;
  const headed = !!scraper?.auth?.headed;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-2 sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-label={`Sign in to ${name}`}
    >
      <div className="absolute inset-0 bg-black/70" aria-hidden="true" />
      <div className="relative z-10 flex flex-col w-[90vw] h-[90vh] max-w-full bg-gray-900 border border-gray-700 rounded-xl shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-start gap-3 px-4 py-3 border-b border-gray-800 shrink-0">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-base font-semibold text-white truncate">Sign in: {name}</h2>
              {state && (
                <span
                  data-testid="login-state"
                  className={clsx(
                    "text-[10px] px-1.5 py-0.5 rounded border",
                    STATE_CLASSES[state] ?? "bg-gray-800 text-gray-300 border-gray-700",
                  )}
                >
                  {STATE_LABELS[state] ?? state}
                </span>
              )}
              {timeLeft && (
                <span className="text-xs text-gray-400 tabular-nums" title="Time left in this session">
                  {timeLeft} left
                </span>
              )}
              {active && !socket.connected && socket.session && (
                <span className="text-xs text-amber-400">Reconnecting…</span>
              )}
            </div>
            {headed && active && (
              <p className="text-xs text-gray-400 mt-0.5">
                A browser window is also open on the coordinator's display.
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1 text-gray-400 hover:text-white shrink-0 transition-colors"
            aria-label={active ? "Hide login viewer" : "Close login viewer"}
            title={active ? "Hide. The session keeps running; Re-login reopens it." : "Close"}
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        {startError ? (
          <EndPanel
            tone="error"
            message={`Couldn't open a login session: ${startError}`}
            onStartAgain={begin}
            startAgainLabel="Try again"
            starting={starting}
            onClose={onClose}
          />
        ) : !session ? (
          <SpinnerPanel message="Starting the browser…" />
        ) : ended ? (
          <EndPanel
            tone={session.state === "succeeded" ? "success" : "error"}
            message={session.message ?? END_MESSAGES[session.state] ?? "The login session ended."}
            onStartAgain={session.state === "succeeded" ? undefined : begin}
            starting={starting}
            onClose={onClose}
          />
        ) : CONFIRMING_STATES.has(session.state) ? (
          <SpinnerPanel
            message={session.message ?? "Running one scrape to confirm the sign-in…"}
          />
        ) : (
          <>
            <LoginToolbar
              pages={socket.pages}
              send={socket.send}
              canCheck={session.can_check && session.state === "waiting_for_user"}
              checkTitle={
                session.can_check
                  ? "Open the verify page and look for the signed-in content"
                  : "This scraper's auth block has no verify.url"
              }
              checking={checking || session.state === "checking"}
              onCheck={handleCheck}
              cancelling={cancelRequested}
              onCancel={handleCancel}
            />
            {(session.message || actionError) && (
              <div className="px-4 py-1.5 text-xs border-b border-gray-800 shrink-0" role="status">
                {actionError ? (
                  <span className="text-red-400">{actionError}</span>
                ) : (
                  <span className="text-gray-300">{session.message}</span>
                )}
              </div>
            )}
            <LoginViewer frame={socket.frame} send={socket.send} />
          </>
        )}
      </div>
    </div>
  );
}

/** The current time, ticking every second while `running`. */
function useNow(running: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [running]);
  return now;
}

function SpinnerPanel({ message }: { message: string }) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center gap-3 p-6 text-center" role="status">
      <Loader2 size={28} className="animate-spin text-indigo-400" aria-hidden="true" />
      <p className="text-sm text-gray-300 max-w-md">{message}</p>
    </div>
  );
}

function EndPanel({
  tone,
  message,
  onStartAgain,
  startAgainLabel = "Start again",
  starting,
  onClose,
}: {
  tone: "success" | "error";
  message: string;
  onStartAgain?: () => void;
  startAgainLabel?: string;
  starting: boolean;
  onClose: () => void;
}) {
  const Icon = tone === "success" ? CheckCircle2 : AlertTriangle;
  return (
    <div className="flex-1 flex flex-col items-center justify-center gap-4 p-6 text-center">
      <Icon
        size={32}
        className={tone === "success" ? "text-green-400" : "text-amber-400"}
        aria-hidden="true"
      />
      <p className="text-sm text-gray-200 max-w-lg" data-testid="login-end-message">
        {message}
      </p>
      <div className="flex gap-2">
        {onStartAgain && (
          <button
            type="button"
            onClick={onStartAgain}
            disabled={starting}
            className="px-4 py-2 rounded text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-500 transition-colors disabled:opacity-60"
          >
            {starting ? "Starting…" : startAgainLabel}
          </button>
        )}
        <button
          type="button"
          onClick={onClose}
          className="px-4 py-2 rounded text-sm font-medium text-gray-200 bg-gray-700 hover:bg-gray-600 transition-colors"
        >
          Close
        </button>
      </div>
    </div>
  );
}

// ─── Toolbar ──────────────────────────────────────────────────────────────────

function LoginToolbar({
  pages,
  send,
  canCheck,
  checkTitle,
  checking,
  onCheck,
  cancelling,
  onCancel,
}: {
  pages: ScraperLoginPage[];
  send: (command: ScraperLoginCommand) => boolean;
  canCheck: boolean;
  checkTitle: string;
  checking: boolean;
  onCheck: () => void;
  cancelling: boolean;
  onCancel: () => void;
}) {
  const activePage = pages.find((p) => p.active) ?? pages[0];
  return (
    <div className="flex flex-wrap items-center gap-2 px-4 py-2 border-b border-gray-800 shrink-0">
      {pages.length > 0 && (
        <select
          aria-label="Browser tab"
          value={activePage?.id ?? ""}
          onChange={(e) => send({ cmd: "switch_page", id: e.target.value })}
          title={activePage?.url}
          className="max-w-[16rem] truncate bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-200"
        >
          {pages.map((p) => (
            <option key={p.id} value={p.id}>
              {p.title || p.url || "(blank tab)"}
            </option>
          ))}
        </select>
      )}
      <button type="button" className={TOOL_BUTTON} onClick={() => send({ cmd: "history", action: "back" })}>
        <ArrowLeft size={13} aria-hidden="true" /> Back
      </button>
      <button type="button" className={TOOL_BUTTON} onClick={() => send({ cmd: "history", action: "reload" })}>
        <RotateCw size={13} aria-hidden="true" /> Reload
      </button>
      <button type="button" className={TOOL_BUTTON} onClick={() => send({ cmd: "navigate", target: "login" })}>
        <LogIn size={13} aria-hidden="true" /> Go to sign-in page
      </button>
      <button
        type="button"
        className={TOOL_BUTTON}
        onClick={onCheck}
        disabled={!canCheck || checking}
        title={checkTitle}
      >
        <ShieldCheck size={13} aria-hidden="true" /> {checking ? "Checking…" : "Check now"}
      </button>
      <button
        type="button"
        className={clsx(TOOL_BUTTON, "hover:bg-red-900/60 hover:border-red-800")}
        onClick={onCancel}
        disabled={cancelling}
      >
        {cancelling ? "Cancelling…" : "Cancel"}
      </button>
    </div>
  );
}

// ─── Viewer: the screencast plus pointer and keyboard forwarding ──────────────

interface HeldKey {
  key: string;
  code?: string;
}

function LoginViewer({
  frame,
  send,
}: {
  frame: ScraperLoginFrame | null;
  send: (command: ScraperLoginCommand) => boolean;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const overlayRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [natural, setNatural] = useState<FrameSize | null>(null);
  const [box, setBox] = useState<FrameSize | null>(null);
  const [focused, setFocused] = useState(false);
  const [softKeyboard, setSoftKeyboard] = useState(false);

  const hasFrame = frame !== null;
  const frameSize = frameSizeOf(frame, natural);
  const frameSizeRef = useRef(frameSize);
  frameSizeRef.current = frameSize;
  const sendRef = useRef(send);
  sendRef.current = send;

  // Keys sent down and not yet up, by code (or key when there's no code).
  const heldRef = useRef(new Map<string, HeldKey>());
  const buttonsDownRef = useRef(new Set<ScraperLoginMouseButton>());
  const lastPointRef = useRef<{ x: number; y: number } | null>(null);
  const clickRef = useRef({ time: 0, x: 0, y: 0, button: "left" as ScraperLoginMouseButton, count: 0 });
  // One move and one accumulated wheel per animation frame.
  const pendingMoveRef = useRef<{ x: number; y: number } | null>(null);
  const pendingWheelRef = useRef<{ x: number; y: number; dx: number; dy: number } | null>(null);
  const rafRef = useRef<number | null>(null);

  const flushPointer = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    const move = pendingMoveRef.current;
    pendingMoveRef.current = null;
    if (move) sendRef.current({ cmd: "mouse", action: "move", x: move.x, y: move.y });
    const wheel = pendingWheelRef.current;
    pendingWheelRef.current = null;
    if (wheel) {
      sendRef.current({
        cmd: "wheel",
        x: wheel.x,
        y: wheel.y,
        dx: clampWheel(wheel.dx),
        dy: clampWheel(wheel.dy),
      });
    }
  }, []);

  const scheduleFlush = useCallback(() => {
    if (rafRef.current !== null) return;
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = null;
      flushPointer();
    });
  }, [flushPointer]);

  useEffect(
    () => () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    },
    [],
  );

  const toPage = useCallback((clientX: number, clientY: number) => {
    const el = overlayRef.current;
    if (!el) return null;
    return pointerToPage(clientX, clientY, el.getBoundingClientRect(), frameSizeRef.current);
  }, []);

  const focusKeyboard = useCallback(() => {
    textareaRef.current?.focus({ preventScroll: true });
  }, []);

  // Fit the image to the space available, keeping the page's aspect ratio.
  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const measure = () => setBox({ width: el.clientWidth, height: el.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Wheel needs a non-passive listener so the modal doesn't scroll instead.
  useEffect(() => {
    const el = overlayRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const p = toPage(e.clientX, e.clientY);
      if (!p) return;
      const { dx, dy } = wheelPixels(e, frameSizeRef.current);
      const prev = pendingWheelRef.current;
      pendingWheelRef.current = {
        x: p.x,
        y: p.y,
        dx: (prev?.dx ?? 0) + dx,
        dy: (prev?.dy ?? 0) + dy,
      };
      scheduleFlush();
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
    // The overlay mounts with the first frame.
  }, [toPage, scheduleFlush, hasFrame]);

  // Soft keyboards often report Backspace/Enter only as input events.
  useEffect(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    const onBeforeInput = (e: InputEvent) => {
      const key =
        e.inputType === "deleteContentBackward"
          ? "Backspace"
          : e.inputType === "insertLineBreak" || e.inputType === "insertParagraph"
            ? "Enter"
            : null;
      if (!key) return;
      e.preventDefault();
      sendRef.current({ cmd: "key", action: "down", key, code: key });
      sendRef.current({ cmd: "key", action: "up", key, code: key });
    };
    ta.addEventListener("beforeinput", onBeforeInput);
    return () => ta.removeEventListener("beforeinput", onBeforeInput);
  }, []);

  // Keyboard focus starts in the viewer, so typing goes to the page at once.
  useEffect(() => {
    focusKeyboard();
  }, [focusKeyboard]);

  const sendText = useCallback((text: string) => {
    for (const chunk of chunkText(text)) sendRef.current({ cmd: "text", text: chunk });
  }, []);

  const releaseHeldKeys = useCallback(() => {
    const held = [...heldRef.current.values()];
    heldRef.current.clear();
    for (const k of held) {
      sendRef.current({ cmd: "key", action: "up", key: k.key, ...(k.code ? { code: k.code } : {}) });
    }
  }, []);

  // The viewer goes away mid-press when the session moves on (verified, confirming).
  useEffect(() => releaseHeldKeys, [releaseHeldKeys]);

  // ── pointer ──

  function onPointerDown(e: React.PointerEvent<HTMLDivElement>) {
    e.preventDefault();
    focusKeyboard();
    const button = mouseButtonOf(e.button);
    const p = toPage(e.clientX, e.clientY);
    if (!button || !p) return;
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      // Not every environment supports capture; the drag just ends at the edge.
    }
    flushPointer();
    const last = clickRef.current;
    const repeat =
      last.button === button &&
      e.timeStamp - last.time <= DOUBLE_CLICK_MS &&
      Math.abs(p.x - last.x) <= DOUBLE_CLICK_SLOP_PX &&
      Math.abs(p.y - last.y) <= DOUBLE_CLICK_SLOP_PX;
    const count = repeat ? Math.min(last.count + 1, MAX_CLICK_COUNT) : 1;
    clickRef.current = { time: e.timeStamp, x: p.x, y: p.y, button, count };
    buttonsDownRef.current.add(button);
    lastPointRef.current = p;
    send({ cmd: "mouse", action: "down", x: p.x, y: p.y, button, click_count: count });
  }

  function onPointerMove(e: React.PointerEvent<HTMLDivElement>) {
    const p = toPage(e.clientX, e.clientY);
    if (!p) return;
    lastPointRef.current = p;
    pendingMoveRef.current = p;
    scheduleFlush();
  }

  function onPointerUp(e: React.PointerEvent<HTMLDivElement>) {
    const button = mouseButtonOf(e.button);
    const p = toPage(e.clientX, e.clientY) ?? lastPointRef.current;
    if (!button || !p || !buttonsDownRef.current.has(button)) return;
    flushPointer();
    buttonsDownRef.current.delete(button);
    send({ cmd: "mouse", action: "up", x: p.x, y: p.y, button, click_count: clickRef.current.count || 1 });
  }

  function onPointerCancel() {
    const p = lastPointRef.current;
    flushPointer();
    for (const button of buttonsDownRef.current) {
      if (p) send({ cmd: "mouse", action: "up", x: p.x, y: p.y, button, click_count: 1 });
    }
    buttonsDownRef.current.clear();
  }

  // ── keyboard ──

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    const native = e.nativeEvent;
    // IME composition, dead keys and soft keyboards: the text arrives as
    // input/compositionend, so leave these keys to the browser.
    if (
      native.isComposing ||
      e.key === "Process" ||
      e.key === "Unidentified" ||
      e.key === "Dead" ||
      native.keyCode === 229
    ) {
      return;
    }
    // Ctrl/Cmd+V: let the paste event carry the text instead of a remote Ctrl+V.
    if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "v") return;
    e.preventDefault();
    const code = e.code || undefined;
    heldRef.current.set(code ?? e.key, { key: e.key, code });
    send({ cmd: "key", action: "down", key: e.key, ...(code ? { code } : {}) });
  }

  function onKeyUp(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    const id = e.code || e.key;
    const held = heldRef.current.get(id);
    if (!held) return;
    e.preventDefault();
    if (e.key === "Meta") {
      // macOS sends no keyup for keys pressed with Cmd held; release them with it.
      heldRef.current.delete(id);
      releaseHeldKeys();
    } else {
      heldRef.current.delete(id);
    }
    send({ cmd: "key", action: "up", key: held.key, ...(held.code ? { code: held.code } : {}) });
  }

  function onPaste(e: React.ClipboardEvent<HTMLTextAreaElement>) {
    e.preventDefault();
    const text = e.clipboardData?.getData("text/plain") || e.clipboardData?.getData("text") || "";
    if (text) sendText(text);
  }

  function onInput(e: React.FormEvent<HTMLTextAreaElement>) {
    if ((e.nativeEvent as InputEvent).isComposing) return;
    const ta = e.currentTarget;
    const value = ta.value;
    ta.value = "";
    if (value) sendText(value);
  }

  function onCompositionEnd(e: React.CompositionEvent<HTMLTextAreaElement>) {
    const ta = e.currentTarget;
    const value = ta.value || e.data;
    ta.value = "";
    if (value) sendText(value);
  }

  function onBlur() {
    setFocused(false);
    setSoftKeyboard(false);
    releaseHeldKeys();
  }

  function openSoftKeyboard() {
    flushSync(() => setSoftKeyboard(true));
    focusKeyboard();
  }

  // Explicit size: the largest box with the page's aspect ratio that fits (never upscaled).
  const scale = box
    ? Math.min(box.width / frameSize.width, box.height / frameSize.height, 1)
    : 1;
  const displayed = box
    ? { width: Math.floor(frameSize.width * scale), height: Math.floor(frameSize.height * scale) }
    : null;

  return (
    <div className="relative flex-1 min-h-0 flex flex-col bg-black">
      <div className="flex items-center justify-between gap-2 px-4 py-1 text-[11px] text-gray-500 shrink-0">
        <span>
          {focused
            ? "Typing goes to the page. Paste with Ctrl/Cmd+V."
            : "Click the page to type into it."}
        </span>
        <button
          type="button"
          onClick={openSoftKeyboard}
          className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-gray-300 bg-gray-800 hover:bg-gray-700"
        >
          <Keyboard size={12} aria-hidden="true" /> Keyboard
        </button>
      </div>
      <div ref={containerRef} className="relative flex-1 min-h-0 flex items-center justify-center overflow-hidden">
        {frame ? (
          <div
            className={clsx("relative", focused ? "ring-2 ring-indigo-500" : "ring-1 ring-gray-700")}
            style={displayed ?? { width: frameSize.width, height: frameSize.height, maxWidth: "100%" }}
          >
            <img
              src={`data:image/jpeg;base64,${frame.data}`}
              alt="The login browser"
              draggable={false}
              onLoad={(e) =>
                setNatural({ width: e.currentTarget.naturalWidth, height: e.currentTarget.naturalHeight })
              }
              className="block w-full h-full select-none pointer-events-none"
            />
            <div
              ref={overlayRef}
              data-testid="login-overlay"
              className="absolute inset-0 cursor-default"
              style={{ touchAction: "none" }}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerCancel={onPointerCancel}
              onContextMenu={(e) => e.preventDefault()}
            />
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2 text-gray-400 text-sm" role="status">
            <Loader2 size={22} className="animate-spin" aria-hidden="true" />
            Waiting for the browser's first frame…
          </div>
        )}
      </div>
      <textarea
        ref={textareaRef}
        aria-label="Keyboard input for the login browser"
        data-testid="login-keyboard"
        className="sr-only"
        inputMode={softKeyboard ? "text" : "none"}
        autoCapitalize="off"
        autoCorrect="off"
        autoComplete="off"
        spellCheck={false}
        onKeyDown={onKeyDown}
        onKeyUp={onKeyUp}
        onPaste={onPaste}
        onInput={onInput}
        onCompositionEnd={onCompositionEnd}
        onFocus={() => setFocused(true)}
        onBlur={onBlur}
      />
    </div>
  );
}
