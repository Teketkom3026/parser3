"""Find contact/team pages on a site."""
from __future__ import annotations

import re
from typing import List, Set, Tuple
from urllib.parse import unquote, urljoin, urlparse


_PATHS = [
    # ── Топ-приоритет: руководство / команда ──────────────────────────────────────────
    "/rukovodstvo", "/руководство", "/management", "/leadership",
    # Образовательные сайты (/sveden/ — обязательная структура по приказу Минобрнауки)
    "/sveden/managers",       # Руководство
    "/sveden/rukovod",        # вариант написания
    "/sveden/employees",      # Педагогический состав
    "/sveden/struct",         # Структура и органы управления
    # Вложенные пути management
    "/about/management", "/about/rukovodstvo",
    "/company/management", "/company/team",
    "/kontakty/rukovodstvo", "/contacts/management",
    "/о-компании/руководство",
    # Гос. / муниципальные
    "/administration", "/administraciya",
    "/о-школе/руководство", "/o-shkole/rukovodstvo",
    "/university/management", "/universitet/rukovodstvo",
    # ── contacts / about ──────────────────────────────────────────────────────────────
    "/contacts", "/contact", "/контакты", "/kontakty",
    "/about", "/о-компании", "/o-kompanii", "/о-нас", "/ob-organizacii",
    # ── team / staff ──────────────────────────────────────────────────────────────────
    "/team", "/команда", "/komanda", "/nasha-komanda", "/our-team",
    "/staff", "/сотрудники", "/предприятие",
    "/about/team", "/about/staff", "/company/staff",
    "/struktura", "/структура", "/departments", "/departamenty",
    "/подразделения", "/otdely", "/отделы",
    "/specialists", "/специалисты", "/experts", "/эксперты",
    # ── press / news / careers ────────────────────────────────────────────────────────
    "/press", "/press-center", "/news", "/пресс-центр", "/press-relizy",
    "/vacancies", "/вакансии", "/careers", "/карьера",
    # ── history / requisites ──────────────────────────────────────────────────────────
    "/history", "/история", "/requisites", "/реквизиты", "/rekvizity",
]

_KEYWORD_RE = re.compile(
    r"(contact|контакт|kontakt|team|команд|about|о\s*нас|о\s*компании|"
    r"rukovodstv|руководств|staff|сотрудник|management|менеджмент|leadership|"
    r"press|пресс|news|новост|vacanc|вакан|career|карьер|"
    r"struktur|структур|department|департамент|подразд|otdel|отдел|"
    r"specialist|специалист|expert|эксперт|"
    r"истори|history|rekvizit|реквизит|organizacii|"
    r"sveden|сведени|administrac|администрац|"
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


# П.1.3: news / history / careers / vacancies / year-archive URLs must never enter
# the обход — they bring quotes, press releases and SEO names, not real contacts.
# Matched after unquote() so both raw-cyrillic and percent-encoded paths are caught.
_JUNK_URL_RE = re.compile(
    r"(?:/новост|/news|/media/news|"
    r"/istoriya|/history|/истори|"
    r"/karera|/career|/карьер|"
    r"/vakansii|/vacanc|/вакан|"
    r"/(?:19|20)\d{2}(?:/|$))",
    re.IGNORECASE,
)


def _score_url(u: str) -> int:
    """Priority score for a candidate URL.

    Higher is better. The caller sorts DESC and returns the top-N.
    Junk pages (news/history/careers/vacancies/year archives) get a strong
    negative score so they sort last and are dropped from the обход (П.1.3).
    """
    low = unquote(u).lower()
    if _JUNK_URL_RE.search(low):
        return -100
    score = 0
    # HIGH: leadership / management / директор + образовательные /sveden/managers
    for kw in ("rukovodstv", "руководств", "management", "leadership", "директор",
               "sveden/manag", "sveden/rukov", "administrac", "администрац"):
        if kw in low:
            score += 20
            break
    # MED: team / staff / sveden/employees
    for kw in ("team", "команд", "staff", "сотрудник", "nasha-komanda", "our-team",
               "sveden/employ", "sveden/struct"):
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
               "rekvizit", "реквизит", "ob-organizacii", "sveden"):
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
    # Sort candidates by score DESC. Drop junk pages (П.1.3: negative score —
    # news/history/careers/vacancies/year archives) so they never enter the обход.
    ranked: List[Tuple[int, str]] = sorted(
        ((_score_url(u), u) for u in found), key=lambda x: (-x[0], x[1])
    )
    return [u for s, u in ranked[:max_urls] if s >= 0]


def guess_contact_urls(base_url: str) -> List[str]:
    """Generate guesses for standard contact paths."""
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    return [root + p for p in _PATHS]
