"""HTTP fetcher with optional Playwright fallback."""
from __future__ import annotations

import asyncio
import random
from typing import Optional

import httpx

from backend.core.config import settings
from backend.core.logging import get_logger


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
]

log = get_logger("fetcher")


class BrowserPool:
    """Lightweight Playwright pool."""
    def __init__(self, size: int = 2):
        self.size = size
        self._pw = None
        self._browser = None
        self._contexts = None
        self._lock = asyncio.Lock()

    async def start(self):
        async with self._lock:
            if self._browser is not None:
                return
            try:
                from playwright.async_api import async_playwright
                self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                self._contexts = asyncio.Queue(maxsize=self.size)
                for _ in range(self.size):
                    ctx = await self._browser.new_context(
                        user_agent=random.choice(USER_AGENTS),
                        ignore_https_errors=True,
                    )
                    await self._contexts.put(ctx)
                log.info("browser_pool_started", size=self.size)
            except Exception as e:
                log.warning("browser_pool_disabled", error=str(e))
                self._browser = None

    async def stop(self):
        try:
            if self._contexts:
                while not self._contexts.empty():
                    ctx = await self._contexts.get()
                    await ctx.close()
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass

    async def fetch(self, url: str, timeout: int = 25) -> Optional[str]:
        if not self._browser:
            return None
        ctx = await self._contexts.get()
        page = None
        try:
            page = await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            # small wait for dynamic content
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            html = await page.content()
            return html
        except Exception as e:
            log.warning("browser_fetch_error", url=url, error=str(e))
            return None
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
            try:
                await ctx.clear_cookies()
            except Exception:
                pass
            await self._contexts.put(ctx)


class Fetcher:
    def __init__(self, browser_pool: Optional[BrowserPool] = None):
        self.browser_pool = browser_pool
        self._client: Optional[httpx.AsyncClient] = None

    async def start(self):
        self._client = httpx.AsyncClient(
            headers={"User-Agent": random.choice(USER_AGENTS), "Accept-Language": "ru,en;q=0.9"},
            timeout=httpx.Timeout(settings.crawler_page_timeout_sec),
            follow_redirects=True,
        )

    async def stop(self):
        if self._client:
            await self._client.aclose()

    async def fetch(self, url: str, force_browser: bool = False) -> Optional[str]:
        html = None
        if not force_browser:
            try:
                r = await self._client.get(url)
                if r.status_code == 200:
                    html = r.text
            except Exception as e:
                log.info("httpx_error", url=url, error=str(e))

        # SPA detection fallback
        def is_spa(h: str) -> bool:
            if not h or len(h) < 2000:
                return True
            low = h.lower()
            if 'id="root"' in low and len(low) < 8000:
                return True
            if 'reactdom' in low and '<body' in low:
                body_text_len = len(low.split("<body", 1)[1])
                if body_text_len < 3000:
                    return True
            return False

        if (html is None or is_spa(html)) and self.browser_pool and settings.fetch_use_browser:
            browser_html = await self.browser_pool.fetch(url, timeout=settings.crawler_page_timeout_sec)
            if browser_html:
                html = browser_html
        return html
