"""Find contact/team pages on a site."""
from __future__ import annotations

import re
from typing import List, Set, Tuple
from urllib.parse import urljoin, urlparse


_PATHS = [
    # contacts / about
    "/contacts", "/contact", "/контакты", "/kontakty",
    "/about", "/о-компании", "/o-kompanii", "/о-нас", "/ob-organizacii",
    # team / structure
    "/team", "/команда", "/komanda", "/nasha-komanda", "/our-team",
    "/rukovodstvo", "/руководство", "/management", "/leadership",
    "/staff", "/сотрудники", "/предприятие",
    "/struktura", "/структура", "/departments", "/departamenty",
    "/подразделения", "/otdely", "/отделы",
    "/specialists", "/специалисты", "/experts", "/эксперты",
    # press / news / careers
    "/press", "/press-center", "/news", "/пресс-центр", "/press-relizy",
    "/vacancies", "/вакансии", "/careers", "/карьера",
    # history / requisites
    "/history", "/история", "/requisites", "/реквизиты", "/rekvizity",
]

_KEYWORD_RE = re.compile(
    r"(contact|контакт|kontakt|team|команд|about|о\s*нас|о\s*компании|"
    r"rukovodstv|руководств|staff|сотрудник|management|менеджмент|leadership|"
    r"press|пресс|news|новост|vacanc|вакан|career|карьер|"
    r"struktur|структур|department|департамент|подразд|otdel|отдел|"
    r"specialist|специалист|expert|эксперт|"
    r"истори|history|rekvizit|реквизит|organizacii|"
    r"our|наш)",
    re.IGNORECASE,
)


def _same_domain(a: str, b: str) -> bool:
    try:
        def _host(u):
            h = urlparse(u).netloc.lower()
            return h[4:] if h.startswith("www.") else h
        return _host(a) == _host(b)
    except Exception:
        return False


def _score_url(u: str) -> int:
    """Priority score for a candidate URL.

    Higher is better. The caller sorts DESC and returns the top-N.
    """
    low = u.lower()
    score = 0
    # HIGH: leadership / management / директор
    for kw in ("rukovodstv", "руководств", "management", "leadership", "директор"):
        if kw in low:
            score += 20
            break
    # MED: team / staff
    for kw in ("team", "команд", "staff", "сотрудник", "nasha-komanda", "our-team"):
        if kw in low:
            score += 10
            break
    # LOW: contacts
    for kw in ("contact", "контакт", "kontakt"):
        if kw in low:
            score += 5
            break
    # BASE: about / press / vacancies / structure etc.
    for kw in ("about", "о-компании", "o-kompanii", "press", "пресс", "news",
               "новост", "vacanc", "вакан", "career", "карьер",
               "struktur", "структур", "department", "департамент",
               "подразд", "specialist", "специалист", "expert", "эксперт",
               "rekvizit", "реквизит", "ob-organizacii"):
        if kw in low:
            score += 3
            break
    return score


def find_contact_urls(html: str, base_url: str, max_urls: int = 8) -> List[str]:
    """Find relevant contact/team pages, sorted by priority score DESC, limited to max_urls."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return []
    found: Set[str] = set()
    # Collect ALL candidates first (do not stop early), then sort by score.
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("mailto:", "tel:", "#", "javascript:")):
            continue
        abs_url = urljoin(base_url, href)
        if not _same_domain(abs_url, base_url):
            continue
        text = (a.get_text(" ", strip=True) or "") + " " + href
        if _KEYWORD_RE.search(text):
            abs_url = abs_url.split("#", 1)[0]
            found.add(abs_url)
    # Sort candidates by score DESC
    ranked: List[Tuple[int, str]] = sorted(
        ((_score_url(u), u) for u in found), key=lambda x: (-x[0], x[1])
    )
    return [u for _, u in ranked[:max_urls]]


def guess_contact_urls(base_url: str) -> List[str]:
    """Generate guesses for standard contact paths."""
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    return [root + p for p in _PATHS]
