"""Find contact/team pages on a site."""
from __future__ import annotations

import re
from typing import List, Set
from urllib.parse import urljoin, urlparse


_PATHS = [
    "/contacts", "/contact", "/контакты", "/kontakty",
    "/about", "/о-компании", "/o-kompanii", "/о-нас",
    "/team", "/команда", "/komanda", "/nasha-komanda", "/our-team",
    "/rukovodstvo", "/руководство", "/management",
    "/staff", "/сотрудники",
    "/leadership", "/предприятие",
    "/requisites", "/реквизиты",
]

_KEYWORD_RE = re.compile(
    r"(contact|контакт|team|команд|about|о\s*нас|о\s*компании|rukovodstv|руководств|"
    r"staff|сотрудник|management|менеджмент|our|наш)",
    re.IGNORECASE,
)


def _same_domain(a: str, b: str) -> bool:
    try:
        return urlparse(a).netloc.lower().lstrip("www.") == urlparse(b).netloc.lower().lstrip("www.")
    except Exception:
        return False


def find_contact_urls(html: str, base_url: str, max_urls: int = 8) -> List[str]:
    """Find relevant contact/team pages."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return []
    found: Set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("mailto:", "tel:", "#", "javascript:")):
            continue
        abs_url = urljoin(base_url, href)
        if not _same_domain(abs_url, base_url):
            continue
        text = (a.get_text(" ", strip=True) or "") + " " + href
        if _KEYWORD_RE.search(text):
            # strip fragment
            abs_url = abs_url.split("#", 1)[0]
            found.add(abs_url)
        if len(found) >= max_urls:
            break
    return list(found)


def guess_contact_urls(base_url: str) -> List[str]:
    """Generate guesses for standard contact paths."""
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    return [root + p for p in _PATHS]
