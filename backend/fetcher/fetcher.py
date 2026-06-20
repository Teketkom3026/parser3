"""HTTP fetcher with optional Playwright fallback.

Supports HTTPS→HTTP fallback for sites with broken/expired SSL or non-HTTPS endpoints.
Caches successful scheme per host to avoid retrying HTTPS on known-broken sites.
"""
from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
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


@dataclass
class FetchResult:
    """Результат fetch с причиной отказа (для гранулярного error_code).

    html   — HTML при успехе, иначе None.
    reason — код причины при html is None (см. backend/core/errors.py); None при успехе.
    status — HTTP-статус последнего ответа, если был (уточняет http_4xx/5xx).
    """
    html: Optional[str] = None
    reason: Optional[str] = None
    status: Optional[int] = None


def _http_reason(status: int) -> str:
    """Код причины по HTTP-статусу."""
    if status == 403:
        return "http_403"
    if status == 429:
        return "http_429"
    if 400 <= status < 500:
        return "http_4xx"
    if 500 <= status < 600:
        return "http_5xx"
    return "fetch_failed"


def _classify_exc(exc: Exception) -> str:
    """Код причины по исключению httpx (соединение/таймаут/SSL)."""
    if _is_ssl_error(exc):
        return "ssl_error"
    if isinstance(exc, httpx.ConnectTimeout):
        return "connect_timeout"
    if isinstance(exc, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
        return "read_timeout"
    if isinstance(exc, httpx.ConnectError):
        msg = str(exc).lower()
        if "refused" in msg:
            return "conn_refused"
        # DNS-сбой на этапе connect (если precheck выключен / http-fallback на мёртвый host)
        if any(s in msg for s in ("name or service not known", "nodename nor servname",
                                  "temporary failure in name resolution", "no address associated")):
            return "dns_nxdomain"
        return "conn_error"
    if isinstance(exc, httpx.TimeoutException):
        return "read_timeout"
    return "conn_error"


# Типы ресурсов, которые не нужны для извлечения текста — блокируем в браузере ради
# скорости/трафика. JS/XHR/документ НЕ трогаем (иначе сломаем рендер SPA).
_BLOCKED_RESOURCE_TYPES = {"image", "media", "font", "stylesheet"}


async def _block_heavy_resources(route):
    try:
        if route.request.resource_type in _BLOCKED_RESOURCE_TYPES:
            await route.abort()
        else:
            await route.continue_()
    except Exception:
        # страница могла закрыться / route уже обработан — подстрахуемся
        try:
            await route.continue_()
        except Exception:
            pass


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
                    if settings.browser_block_resources:
                        await ctx.route("**/*", _block_heavy_resources)
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
                await page.wait_for_load_state("networkidle", timeout=settings.browser_networkidle_ms)
            except Exception:
                pass
            html = await page.content()
            return html
        except Exception as e:
            log.warning("browser_fetch_error", url=url, error=str(e))
            return None
        finally:
            # Возврат слота в пул ОБЯЗАН произойти, даже если эту корутину отменяют
            # (пер-сайт `asyncio.wait_for` в task_manager). Раньше очистка/возврат шли
            # через `await`, и отмена (или зависший page.close) могла прервать finally
            # ДО `await self._contexts.put(ctx)` → слот терялся. После browser_pool_size
            # таких случаев `_contexts.get()` блокируется навсегда → все воркеры висят
            # (репорт «парсер зависает на малых объёмах»). Чистку таймбоксим и глушим
            # ЛЮБОЕ исключение (вкл. CancelledError), а слот кладём синхронно put_nowait
            # (место гарантировано — мы его только что взяли).
            if page:
                try:
                    await asyncio.wait_for(page.close(), timeout=5)
                except BaseException:
                    pass
            try:
                await asyncio.wait_for(ctx.clear_cookies(), timeout=5)
            except BaseException:
                pass
            try:
                self._contexts.put_nowait(ctx)
            except asyncio.QueueFull:
                pass


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
            # G2: connect-таймаут короче read-таймаута. Висящий/мёртвый хост отваливается
            # за ~8с на connect, а не держит слот полные 25с; живой, но медленно отдающий
            # страницу сайт по-прежнему получает 25с на чтение.
            timeout=httpx.Timeout(
                settings.crawler_page_timeout_sec,
                connect=settings.crawler_connect_timeout_sec,
            ),
            follow_redirects=True,
            # Кап на цепочку редиректов: без него редирект-петля × (connect+read) даёт
            # десятки-сотни секунд на один URL (видно в max времени httpx).
            max_redirects=5,
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
        """Тонкая обёртка над fetch_result — возвращает только HTML (или None)."""
        return (await self.fetch_result(url, force_browser)).html

    async def fetch_result(self, url: str, force_browser: bool = False) -> FetchResult:
        """Как fetch, но с причиной отказа (reason) и HTTP-статусом для error_code.

        Инструментирование: на каждый фетч пишем строку `fetch_done` (via=httpx|browser|none,
        статус, причина, мс, длина html) — для профилирования (сколько уходит в браузер,
        средн./p95 время). Фильтровать в логах: `grep fetch_done`.
        """
        t0 = time.monotonic()
        res, via = await self._fetch_inner(url, force_browser)
        log.info(
            "fetch_done",
            host=urlparse(url).netloc,
            via=via,
            status=res.status,
            reason=res.reason,
            ms=int((time.monotonic() - t0) * 1000),
            html_len=(len(res.html) if res.html else 0),
        )
        return res

    async def _fetch_inner(self, url: str, force_browser: bool = False) -> tuple[FetchResult, str]:
        """Логика фетча. Возвращает (результат, via) — via: 'httpx' | 'browser' | 'none'."""
        html: Optional[str] = None
        last_status: Optional[int] = None
        last_err: Optional[Exception] = None
        via = "none"

        if not force_browser:
            # Check per-host cache: if we know HTTPS doesn't work on this host,
            # go straight to HTTP.
            parsed = urlparse(url)
            effective_url = url
            if parsed.scheme == "https" and parsed.netloc in self._http_only_hosts:
                effective_url = _to_http(url)
                log.info("https_skip_cached_http_only", host=parsed.netloc)

            html, err, last_status = await self._try_http_get(effective_url)
            last_err = err

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
                html, err2, status2 = await self._try_http_get(http_url)
                if html is not None:
                    # Remember: this host needs HTTP for next pages.
                    self._http_only_hosts.add(parsed.netloc)
                    last_err, last_status = None, status2
                else:
                    # причина/статус итоговой (http) попытки информативнее
                    last_err = err2 if err2 is not None else err
                    last_status = status2 if status2 is not None else last_status
                    if err2 is not None:
                        log.info("http_fallback_also_failed", url=http_url, error=str(err2)[:200])
            elif html is None and err is not None:
                log.info("httpx_error", url=effective_url, error=str(err)[:200])

            if html is not None:
                via = "httpx"

            # If httpx got a definitive 4xx — the page truly does not exist.
            # Don't waste ~25s in the browser for a non-existent URL.
            if html is None and last_status is not None and 400 <= last_status < 500:
                log.info("skip_browser_on_4xx", url=url, status=last_status)
                return FetchResult(None, _http_reason(last_status), last_status), "httpx"

            # Сетево-мёртвый хост: если httpx упал на этапе СОЕДИНЕНИЯ (connect timeout /
            # refused / DNS / no route), браузер пойдёт на тот же недоступный хост и
            # просто сожжёт навигационный таймаут (~25с впустую). Скипаем браузер —
            # мёртвый сайт теперь ~connect_timeout вместо connect+nav (профиль 10k:
            # каждый недоступный сайт был ~33с). SSL/read_timeout НЕ скипаем: там хост
            # достижим (медленный/JS/битый серт) и браузер может дотянуть.
            if html is None and last_err is not None:
                _reason = _classify_exc(last_err)
                if _reason in ("connect_timeout", "conn_refused", "dns_nxdomain", "conn_error"):
                    log.info("skip_browser_on_netfail", url=url, reason=_reason)
                    return FetchResult(None, _reason, last_status), "none"

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
                last_err = None  # браузер дотянул — отказа нет
                via = "browser"

        if html:
            return FetchResult(html, None, last_status), via
        # Отказ — классифицируем причину для error_code.
        if last_status is not None and 400 <= last_status < 600:
            return FetchResult(None, _http_reason(last_status), last_status), "httpx"
        if last_err is not None:
            return FetchResult(None, _classify_exc(last_err), last_status), "none"
        return FetchResult(None, "empty_html", last_status), "none"
