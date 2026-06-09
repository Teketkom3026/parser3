#!/usr/bin/env python3
"""Скачать сырой HTML страницы и сохранить как golden-фикстуру.

Использование:
    python tools/save_fixture.py <url> [имя_без_расширения] [--browser]

Примеры:
    python tools/save_fixture.py https://www.rikor-electronics.ru/kontakty rikor_kontakty
    python tools/save_fixture.py https://sosnovgeo.ru/page/mmenu/kontakty.html --browser

Файл кладётся в tests/fixtures/html/<имя>.html (имя берётся из URL, если не задано).

Логика fetch повторяет прод по духу: браузерный User-Agent + httpx, при пустом/JS-only
ответе — fallback на Playwright (если установлен), как делает BrowserPool в проде.
Флаг --browser форсирует Playwright сразу (для заведомо JS-сайтов).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlparse

_ROOT = Path(__file__).resolve().parent.parent
_FIX_DIR = _ROOT / "tests" / "fixtures" / "html"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _name_from_url(url: str) -> str:
    p = urlparse(url)
    base = (p.netloc + p.path).replace("www.", "")
    base = re.sub(r"[^a-zA-Z0-9]+", "_", base).strip("_").lower()
    return base or "fixture"


def _looks_empty(html: str | None) -> bool:
    return not html or len(html) < 2000


def _fetch_httpx(url: str) -> str | None:
    try:
        import httpx
    except ImportError:
        return None
    headers = {"User-Agent": _UA, "Accept-Language": "ru,en;q=0.9"}
    for candidate in (url, re.sub(r"^https://", "http://", url)):
        try:
            # verify=False: RU-корп/гос-сайты часто с битым/самоподписанным сертом;
            # прод тоже их тянет (http-fallback + браузер ignore_https_errors). Для
            # скачивания публичного HTML-фикстура проверка серта не нужна.
            r = httpx.get(candidate, headers=headers, follow_redirects=True,
                          timeout=25, verify=False)
            if r.status_code == 200 and r.text:
                return r.text
            print(f"  httpx {candidate} → HTTP {r.status_code}")
        except Exception as e:  # noqa: BLE001
            print(f"  httpx {candidate} → {type(e).__name__}: {str(e)[:120]}")
        if not candidate.startswith("https://"):
            break
    return None


def _fetch_browser(url: str) -> str | None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  playwright не установлен — пропускаю браузерный fallback")
        return None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True,
                                         args=["--no-sandbox", "--disable-dev-shm-usage"])
            ctx = browser.new_context(user_agent=_UA, ignore_https_errors=True)
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=25000)
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:  # noqa: BLE001
                pass
            html = page.content()
            browser.close()
            return html
    except Exception as e:  # noqa: BLE001
        print(f"  browser → {type(e).__name__}: {str(e)[:120]}")
        return None


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--browser"]
    force_browser = "--browser" in argv
    if not args:
        print(__doc__)
        return 2
    url = args[0]
    name = args[1] if len(args) > 1 else _name_from_url(url)

    print(f"Скачиваю: {url}")
    html = None
    if not force_browser:
        html = _fetch_httpx(url)
    if force_browser or _looks_empty(html):
        print("  → пробую браузер (Playwright)")
        browser_html = _fetch_browser(url)
        if browser_html:
            html = browser_html

    if _looks_empty(html):
        print("ОШИБКА: не удалось получить содержательный HTML. "
              "Сохраните вручную (в браузере: «Сохранить страницу как» → HTML).")
        return 1

    _FIX_DIR.mkdir(parents=True, exist_ok=True)
    out = _FIX_DIR / f"{name}.html"
    out.write_text(html, encoding="utf-8")
    print(f"OK: {out}  ({len(html)} байт)")
    print(f"Дальше: добавьте/раскомментируйте кейс в tests/golden/cases.yaml "
          f"(file: {name}.html) и запустите  pytest tests/golden -q")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
