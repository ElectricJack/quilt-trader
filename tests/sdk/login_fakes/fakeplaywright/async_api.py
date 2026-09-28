"""Fake `playwright.async_api` for sdk/scraper_login.py tests (no browser needed).

Only the surface the login helper uses exists. A tiny "site" decides what is
visible: pages under `protected` show `selectors` once the site is signed in.
Typing `password` into the page and pressing Enter signs in and lands on
`protected`, like a login form that redirects. Typing `popup` + Enter opens a
second page (a sign-in popup).

Behaviour comes from a config dict: FakeEngine(config) in-process, or the
FAKE_PLAYWRIGHT environment variable (JSON) when the helper runs as a child
process and imports this module as its `auth.engine`. With `log` set to a
path, every call worth asserting on is appended there as a JSON line.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Optional

KNOWN_KEYS = {
    "Alt", "AltGraph", "CapsLock", "Control", "Meta", "NumLock", "ScrollLock", "Shift",
    "Enter", "Tab", "Backspace", "Delete", "Insert", "Escape",
    "ArrowDown", "ArrowLeft", "ArrowRight", "ArrowUp", "End", "Home", "PageDown", "PageUp",
}

DEFAULT_CONFIG = {
    "signed_in": False,
    "protected": "https://example.test/picks",
    "selectors": ["table.picks"],
    "password": "letmein",
    "viewport": [800, 600],
    "launch_error": None,
    "log": None,
    "quit_on_last_close": True,
}


class _Emitter:
    def __init__(self) -> None:
        self._handlers: dict[str, list] = {}

    def on(self, event: str, handler) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def emit(self, event: str, *args: Any) -> None:
        for handler in list(self._handlers.get(event, [])):
            result = handler(*args)
            if asyncio.iscoroutine(result):
                asyncio.ensure_future(result)


class Site:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.signed_in = bool(config.get("signed_in"))
        self.log_path: Optional[str] = config.get("log")
        self.calls: list[list] = []

    def record(self, *call: Any) -> None:
        self.calls.append(list(call))
        if self.log_path:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(list(call)) + "\n")

    def visible(self, url: str, selector: str) -> bool:
        return (
            self.signed_in
            and url.startswith(self.config["protected"])
            and selector in self.config["selectors"]
        )


class FakeDialog:
    def __init__(self, kind: str, message: str) -> None:
        self.type = kind
        self.message = message
        self.handled: Optional[str] = None

    async def accept(self, prompt_text: Optional[str] = None) -> None:
        self.handled = "accepted"

    async def dismiss(self) -> None:
        self.handled = "dismissed"


class FakeLocator:
    def __init__(self, page: "FakePage", selector: str) -> None:
        self._page = page
        self._selector = selector

    @property
    def first(self) -> "FakeLocator":
        return self

    async def is_visible(self) -> bool:
        if self._selector.startswith("!!"):
            raise ValueError("Unexpected token in selector")
        return self._page.site.visible(self._page.url, self._selector)


class FakeMouse:
    def __init__(self, page: "FakePage") -> None:
        self._page = page

    async def move(self, x: float, y: float) -> None:
        self._page.site.record("mouse.move", self._page.name, x, y)

    async def down(self, button: str = "left", click_count: int = 1) -> None:
        self._page.site.record("mouse.down", self._page.name, button, click_count)

    async def up(self, button: str = "left", click_count: int = 1) -> None:
        self._page.site.record("mouse.up", self._page.name, button, click_count)

    async def wheel(self, delta_x: float, delta_y: float) -> None:
        self._page.site.record("mouse.wheel", self._page.name, delta_x, delta_y)


class FakeKeyboard:
    def __init__(self, page: "FakePage") -> None:
        self._page = page
        self.typed = ""

    def _check(self, key: str) -> None:
        if key not in KNOWN_KEYS and not (len(key) == 1 and " " <= key <= "~"):
            raise ValueError(f'Unknown key: "{key}"')

    async def down(self, key: str) -> None:
        self._check(key)
        self._page.site.record("key.down", self._page.name, key)
        if len(key) == 1:
            self.typed += key
        elif key == "Enter":
            await self._page._submit(self.typed)
            self.typed = ""

    async def up(self, key: str) -> None:
        self._check(key)
        self._page.site.record("key.up", self._page.name, key)

    async def insert_text(self, text: str) -> None:
        self._page.site.record("key.insert_text", self._page.name, text)
        self.typed += text


class FakePage(_Emitter):
    def __init__(self, ctx: "FakeContext", name: str) -> None:
        super().__init__()
        self.ctx = ctx
        self.site = ctx.site
        self.name = name
        self.url = "about:blank"
        self.main_frame = object()
        self.viewport_size = {"width": ctx.site.config["viewport"][0],
                              "height": ctx.site.config["viewport"][1]}
        self.mouse = FakeMouse(self)
        self.keyboard = FakeKeyboard(self)
        self._closed = False
        self._history: list[str] = []
        self._cdp: Optional["FakeCDPSession"] = None

    @property
    def context(self) -> "FakeContext":
        return self.ctx

    def is_closed(self) -> bool:
        return self._closed

    async def title(self) -> str:
        return f"Title of {self.url}"

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    async def evaluate(self, expression: str) -> Any:
        return list(self.site.config["viewport"])

    def _navigated(self, url: str) -> None:
        self.url = url
        self.emit("framenavigated", self.main_frame)
        self.emit("load", self)
        if self._cdp is not None:
            self._cdp.push_frame()

    async def goto(self, url: str, timeout: Optional[float] = None, wait_until: Optional[str] = None) -> None:
        self.site.record("page.goto", self.name, url)
        if url.startswith("https://unreachable.test"):
            raise TimeoutError("Timeout 30000ms exceeded")
        self._history.append(url)
        self._navigated(url)

    async def go_back(self, timeout: Optional[float] = None) -> None:
        self.site.record("page.go_back", self.name)
        if len(self._history) > 1:
            self._history.pop()
            self._navigated(self._history[-1])

    async def go_forward(self, timeout: Optional[float] = None) -> None:
        self.site.record("page.go_forward", self.name)

    async def reload(self, timeout: Optional[float] = None) -> None:
        self.site.record("page.reload", self.name)
        self._navigated(self.url)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self in self.ctx.pages:
            self.ctx.pages.remove(self)
        self.emit("close", self)
        if not self.ctx.pages and self.site.config.get("quit_on_last_close") and not self.ctx.closed:
            await self.ctx.close()

    async def _submit(self, typed: str) -> None:
        if typed == "popup":
            await self.ctx.new_page()
        elif typed == "closepopup":
            await self.close()
        elif typed == "alert":
            self.emit("dialog", FakeDialog("alert", "Hello from the page"))
        elif typed == self.site.config["password"]:
            self.site.signed_in = True
            await self.goto(self.site.config["protected"])

    # Test hooks.
    def open_dialog(self, kind: str, message: str) -> FakeDialog:
        dialog = FakeDialog(kind, message)
        self.emit("dialog", dialog)
        return dialog


class FakeCDPSession(_Emitter):
    def __init__(self, page: FakePage) -> None:
        super().__init__()
        self.page = page
        self.site = page.site
        self._frames = 0
        self.screencasting = False
        self.detached = False

    def push_frame(self) -> None:
        if not self.screencasting:
            return
        self._frames += 1
        width, height = self.site.config["viewport"]
        self.emit("Page.screencastFrame", {
            "data": "ZmFrZS1qcGVn",
            "sessionId": self._frames,
            "metadata": {
                "deviceWidth": width, "deviceHeight": height, "pageScaleFactor": 1,
                "offsetTop": 0, "scrollOffsetX": 0, "scrollOffsetY": 0,
                "timestamp": 1.5, "ignored": True,
            },
        })

    async def send(self, method: str, params: Optional[dict] = None) -> dict:
        self.site.record("cdp.send", self.page.name, method, params)
        if method == "Page.startScreencast":
            self.screencasting = True
            self.page._cdp = self
            self.push_frame()
        elif method == "Page.stopScreencast":
            self.screencasting = False
            if self.page._cdp is self:
                self.page._cdp = None
        return {}

    async def detach(self) -> None:
        self.detached = True
        self.site.record("cdp.detach", self.page.name)


class FakeContext(_Emitter):
    def __init__(self, site: Site) -> None:
        super().__init__()
        self.site = site
        self.pages: list[FakePage] = []
        self.closed = False
        self._seq = 0

    def _make_page(self) -> FakePage:
        self._seq += 1
        page = FakePage(self, f"page{self._seq}")
        self.pages.append(page)
        return page

    async def new_page(self) -> FakePage:
        page = self._make_page()
        self.emit("page", page)
        return page

    async def new_cdp_session(self, page: FakePage) -> FakeCDPSession:
        return FakeCDPSession(page)

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.site.record("ctx.close")
        for page in list(self.pages):
            await page.close()
        self.emit("close", self)


class FakeChromium:
    def __init__(self, site: Site) -> None:
        self.site = site
        self.contexts: list[FakeContext] = []

    async def launch_persistent_context(self, user_data_dir: str, headless: bool = True, **options: Any):
        self.site.record("launch", user_data_dir, headless, options)
        if self.site.config.get("launch_error"):
            raise RuntimeError(self.site.config["launch_error"])
        ctx = FakeContext(self.site)
        ctx._make_page()  # a persistent context starts with one blank page
        self.contexts.append(ctx)
        return ctx


class _Playwright:
    def __init__(self, site: Site) -> None:
        self.chromium = FakeChromium(site)


class _PlaywrightContextManager:
    def __init__(self, site: Site) -> None:
        self._playwright = _Playwright(site)

    async def __aenter__(self) -> _Playwright:
        return self._playwright

    async def __aexit__(self, *exc: Any) -> None:
        return None


class FakeEngine:
    """An `async_api`-shaped object with its own site, for in-process tests."""

    def __init__(self, config: Optional[dict] = None) -> None:
        self.site = Site({**DEFAULT_CONFIG, **(config or {})})
        self.manager: Optional[_PlaywrightContextManager] = None

    def async_playwright(self) -> _PlaywrightContextManager:
        self.manager = _PlaywrightContextManager(self.site)
        return self.manager

    @property
    def context(self) -> FakeContext:
        return self.manager._playwright.chromium.contexts[-1]


def async_playwright() -> _PlaywrightContextManager:
    config = json.loads(os.environ.get("FAKE_PLAYWRIGHT") or "{}")
    return FakeEngine(config).async_playwright()
