"""HTTP fetcher with optional Playwright fallback.

Supports HTTPS→HTTP fallback for sites with broken/expired SSL or non-HTTPS endpoints.
Caches successful scheme per host to avoid retrying HTTPS on known-broken sites.
"""
from __future__ import annotations

import asyncio
import random
from typing import Optional
from urllib.parse import urlparse, urlunparse

import httpx

from backend.core.config import settings
from backend.core.logging import get_logger


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
]

log = get_logger("fetcher")


# Exceptions that justify HTTPS→HTTP fallback.
_HTTPS_FALLBACK_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.ReadError,
    httpx.RemoteProtocolError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
)


def _is_ssl_error(exc: Exception) -> bool:
    """Detect SSL/TLS errors (httpx wraps them in various ways)."""
    msg = str(exc).lower()
    return any(s in msg for s in ("ssl", "certificate", "tls", "handshake"))


def _to_http(url: str) -> str:
    """Replace scheme with http://. Returns same URL if not https."""
    p = urlparse(url)
    if p.scheme != "https":
        return url
    return urlunparse(("http", p.netloc, p.path, p.params, p.query, p.fragment))


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
        # Per-host preferred scheme cache. Once we discover a host only works
        # over http://, we skip the failing https:// on subsequent pages.
        self._http_only_hosts: set[str] = set()

    async def start(self):
        self._client = httpx.AsyncClient(
            headers={"User-Agent": random.choice(USER_AGENTS), "Accept-Language": "ru,en;q=0.9"},
            timeout=httpx.Timeout(settings.crawler_page_timeout_sec),
            follow_redirects=True,
        )

    async def stop(self):
        if self._client:
            await self._client.aclose()

    async def _try_http_get(
        self, url: str
    ) -> tuple[Optional[str], Optional[Exception], Optional[int]]:
        """Single GET attempt. Returns (html_or_none, exception_or_none, status_or_none)."""
        try:
            r = await self._client.get(url)
            if r.status_code == 200:
                return r.text, None, 200
            log.info("httpx_status", url=url, status=r.status_code)
            return None, None, r.status_code
        except Exception as e:
            return None, e, None

    async def fetch(self, url: str, force_browser: bool = False) -> Optional[str]:
        html: Optional[str] = None
        last_status: Optional[int] = None

        if not force_browser:
            # Check per-host cache: if we know HTTPS doesn't work on this host,
            # go straight to HTTP.
            parsed = urlparse(url)
            effective_url = url
            if parsed.scheme == "https" and parsed.netloc in self._http_only_hosts:
                effective_url = _to_http(url)
                log.info("https_skip_cached_http_only", host=parsed.netloc)

            html, err, last_status = await self._try_http_get(effective_url)

            # If HTTPS attempt failed with a connection/SSL error — retry over HTTP.
            if (
                html is None
                and err is not None
                and effective_url.startswith("https://")
                and (isinstance(err, _HTTPS_FALLBACK_EXCEPTIONS) or _is_ssl_error(err))
            ):
                http_url = _to_http(effective_url)
                log.info(
                    "https_fallback_http",
                    url=effective_url,
                    fallback_url=http_url,
                    error=str(err)[:200],
                )
                html, err2, last_status = await self._try_http_get(http_url)
                if html is not None:
                    # Remember: this host needs HTTP for next pages.
                    self._http_only_hosts.add(parsed.netloc)
                elif err2 is not None:
                    log.info("http_fallback_also_failed", url=http_url, error=str(err2)[:200])
            elif html is None and err is not None:
                log.info("httpx_error", url=effective_url, error=str(err)[:200])

            # If httpx got a definitive 4xx — the page truly does not exist.
            # Don't waste ~25s in the browser for a non-existent URL.
            if html is None and last_status is not None and 400 <= last_status < 500:
                log.info("skip_browser_on_4xx", url=url, status=last_status)
                return None

        # SPA detection — same as before.
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
