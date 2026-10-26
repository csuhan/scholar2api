"""Playwright fetcher with reCAPTCHA v2 solving.

Used only when plain HTTP is blocked. Flow on Scholar's block page:
  1. #gs_captcha_c renders a reCAPTCHA v2 checkbox
  2. click it; an image challenge may appear in the "bframe" iframe
  3. tile screenshots are classified by a CaptchaSolver (VLM)
  4. on success the form auto-submits and cookies are exported for reuse
"""
import asyncio
import hashlib
import logging
import os
import random
import re
import threading
from typing import Optional

from ..solvers.base import CaptchaSolver
from .base import BaseFetcher, FetchResult, ScholarBlocked, ScholarHTTPError, detect_block

log = logging.getLogger(__name__)

ARGS = ["--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage", "--no-sandbox"]
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
STEALTH_JS = """
Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});
Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
window.chrome = window.chrome || {runtime:{}};
"""
INSTR = ".rc-imageselect-desc-no-canonical, .rc-imageselect-desc"


class BrowserFetcher(BaseFetcher):
    name = "browser"

    def __init__(self, solver: Optional[CaptchaSolver] = None,
                 headless: Optional[bool] = None, proxy: Optional[str] = None,
                 max_captcha_rounds: int = 3, nav_timeout: float = 45_000):
        self.solver = solver
        self.headless = headless if headless is not None \
            else os.getenv("SCHOLAR2API_HEADLESS", "1") != "0"
        self.proxy = proxy
        self.max_captcha_rounds = max_captcha_rounds
        self.nav_timeout = nav_timeout
        self.task_timeout = float(os.getenv("SCHOLAR2API_BROWSER_TIMEOUT", "300"))
        self.profile_dir = os.getenv("SCHOLAR2API_USER_DATA_DIR")
        self.channel = os.getenv("SCHOLAR2API_CHANNEL") or None
        self._lock = threading.Lock()  # Playwright is not thread-safe
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._pw = self._browser = self._ctx = None

    # ---------------------------------------------------------------- api ---
    def fetch(self, url: str, params: Optional[dict] = None) -> FetchResult:
        with self._lock:
            return self._event_loop().run_until_complete(asyncio.wait_for(
                self._fetch(url, params), timeout=self.task_timeout))

    def close(self) -> None:
        with self._lock:
            if self._pw:
                try:
                    self._event_loop().run_until_complete(self._shutdown())
                except Exception:  # engine already gone
                    self._pw = self._browser = self._ctx = None
            if self._loop and not self._loop.is_closed():
                self._loop.close()
            self._loop = None

    def _event_loop(self) -> asyncio.AbstractEventLoop:
        if not self._loop or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    # ------------------------------------------------------------- engine ---
    async def _ensure_browser(self) -> None:
        if self._ctx:
            return
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        launch = dict(headless=self.headless, args=ARGS, channel=self.channel,
                      proxy={"server": self.proxy} if self.proxy else None)
        context = dict(user_agent=UA, locale="en-US",
                       viewport={"width": 1280, "height": 900})
        if self.profile_dir:  # persistent profile => accumulated trust
            self._ctx = await self._pw.chromium.launch_persistent_context(
                self.profile_dir, **launch, **context)
        else:
            self._browser = await self._pw.chromium.launch(**launch)
            self._ctx = await self._browser.new_context(**context)
        await self._ctx.add_init_script(STEALTH_JS)

    async def _fetch(self, url: str, params: Optional[dict]) -> FetchResult:
        from urllib.parse import urlencode
        await self._ensure_browser()
        if params:
            url = f"{url}?{urlencode(params)}"
        page = await self._ctx.new_page()
        try:
            resp = await page.goto(url, timeout=self.nav_timeout,
                                   wait_until="domcontentloaded")
            await page.wait_for_timeout(random.randint(1000, 2000))
            html = await page.content()

            kind = detect_block(200, page.url, html)
            if kind == "sorry":  # its JS "simple challenge" may pass by itself
                for _ in range(6):
                    await page.wait_for_timeout(2000)
                    if not detect_block(200, page.url, await page.content()):
                        break
                html = await page.content()
                kind = detect_block(200, page.url, html)

            if kind:
                if not self.solver or not self.solver.available():
                    raise ScholarBlocked(f"{kind}_unsolved", html, page.url)
                await self._solve(page)
                await page.wait_for_timeout(random.randint(1000, 2000))
                html = await page.content()
                if remaining := detect_block(200, page.url, html):
                    raise ScholarBlocked(f"{remaining}_after_solve", html, page.url)
            elif resp and resp.status >= 400:
                raise ScholarHTTPError(resp.status, page.url)

            cookies = {c["name"]: c["value"] for c in await self._ctx.cookies()}
            return FetchResult(page.url, 200, html, cookies)
        finally:
            await page.close()

    async def _shutdown(self) -> None:
        for obj in (self._ctx, self._browser):
            if obj:
                await obj.close()
        if self._pw:
            await self._pw.stop()
        self._pw = self._browser = self._ctx = None

    # ------------------------------------------------------------ captcha ---
    async def _solve(self, page) -> None:
        anchor = page.frame(url=re.compile(r"recaptcha.*anchor"))
        for _ in range(20):
            if anchor:
                break
            await page.wait_for_timeout(500)
            anchor = page.frame(url=re.compile(r"recaptcha.*anchor"))
        if not anchor:
            raise ScholarBlocked("captcha_no_anchor", "", page.url)

        await page.wait_for_timeout(random.randint(400, 1200))
        await anchor.click("#recaptcha-anchor")
        await page.wait_for_timeout(random.randint(1500, 2500))

        for _ in range(self.max_captcha_rounds):
            if await self._solved(page, anchor):
                return
            frame = page.frame(url=re.compile(r"recaptcha.*bframe"))
            if not frame:
                await page.wait_for_timeout(1000)
                continue
            await frame.add_style_tag(
                content="*{animation:none!important;transition:none!important}")
            try:
                await self._solve_tiles(frame)
            except Exception as e:
                if type(e).__name__ == "VlmQuotaExhausted":
                    raise ScholarBlocked("vlm_quota_exhausted", "", page.url) from e
                raise
            await page.wait_for_timeout(random.randint(1500, 2500))

        if not await self._solved(page, anchor):
            raise ScholarBlocked("captcha_failed", "", page.url)

    @staticmethod
    async def _solved(page, anchor) -> bool:
        try:
            if await anchor.get_attribute("#recaptcha-anchor", "aria-checked") == "true":
                return True
        except Exception:
            pass
        try:
            return bool(await page.eval_on_selector(
                "#g-recaptcha-response", "el => el.value.length > 0"))
        except Exception:
            return False

    async def _solve_tiles(self, frame) -> None:
        desc = await frame.query_selector(INSTR)
        strong = await desc.query_selector("strong") if desc else None
        raw = (await strong.inner_text() if strong
               else await desc.inner_text() if desc else "").strip()
        target = raw.splitlines()[0].lower() if strong else \
            (re.search(r"with\s+(.+?)(?:\n|$)", raw, re.S) or [raw])[-1].strip().lower()

        table = await frame.query_selector("#rc-imageselect-target table")
        dynamic = "table-44" in (await table.get_attribute("class") or "" if table else "")
        log.info("captcha: target=%r dynamic=%s", target, dynamic)

        shown = {}  # index -> screenshot hash already judged
        for _ in range(12 if dynamic else 1):
            # Screenshot the wrapper, not <img>: in 3x3 grids every <img> is
            # the same composite image cropped by the overflow-hidden wrapper.
            tiles = await frame.query_selector_all(".rc-image-tile-wrapper")
            todo, shots = [], []
            for i, tile in enumerate(tiles):
                try:
                    png = await tile.screenshot(timeout=8000)
                except Exception:
                    continue  # tile is mid-reload
                digest = hashlib.md5(png).hexdigest()
                if dynamic and shown.get(i) == digest:
                    continue  # unchanged -> do not spend another VLM call
                shown[i] = digest
                todo.append(tile)
                shots.append(png)
            if not todo:
                break
            picked = self.solver.select_tiles(shots, target)
            log.info("captcha: %d/%d tiles selected", len(picked), len(todo))
            if not picked:
                break
            for i in picked:
                try:
                    await todo[i].evaluate("el => el.click()")  # survives re-render
                except Exception:
                    pass
                await frame.wait_for_timeout(random.randint(900, 1800))
            if not dynamic:
                break

        try:  # the panel may render outside the viewport
            await frame.evaluate(
                "() => document.getElementById('recaptcha-verify-button').click()")
        except Exception:
            await frame.click("#recaptcha-verify-button", force=True)
