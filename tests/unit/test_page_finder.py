"""page_finder: expanded paths + scoring."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.crawler.page_finder import (
    _KEYWORD_RE, _PATHS, _score_url, find_contact_urls, guess_contact_urls,
)


def test_paths_contain_required_entries():
    """R3: expanded paths catalog must cover press / vacancies / structure / departments."""
    must = [
        "/press", "/press-center", "/news", "/пресс-центр", "/press-relizy",
        "/vacancies", "/вакансии", "/careers",
        "/struktura", "/структура", "/departments", "/departamenty",
        "/подразделения", "/otdely", "/отделы",
        "/specialists", "/специалисты", "/experts", "/эксперты",
        "/history", "/история",
        "/rekvizity", "/ob-organizacii",
    ]
    for p in must:
        assert p in _PATHS, f"Missing {p} in _PATHS"


def test_keyword_regex_matches_new_terms():
    for t in ("press", "пресс", "news", "новости", "вакансии", "careers",
              "структура", "департамент", "подразделение", "специалист",
              "эксперт", "история", "реквизиты"):
        assert _KEYWORD_RE.search(t), f"_KEYWORD_RE must match '{t}'"


def test_scoring_priority_order():
    """Leadership (+20) > Team (+10) > Contact (+5) > About/base (+3)."""
    assert _score_url("/rukovodstvo") >= 20
    assert _score_url("/management") >= 20
    assert _score_url("/team") >= 10
    assert _score_url("/contacts") >= 5
    assert _score_url("/about") >= 3
    assert _score_url("/rukovodstvo") > _score_url("/team")
    assert _score_url("/team") > _score_url("/contacts")
    assert _score_url("/contacts") > _score_url("/about")


def test_find_contact_urls_sorted_by_score():
    html = """
    <html><body>
      <a href="/about">О компании</a>
      <a href="/contact">Контакты</a>
      <a href="/rukovodstvo">Руководство</a>
      <a href="/news">Новости</a>
    </body></html>
    """
    urls = find_contact_urls(html, "https://example.com", max_urls=10)
    assert urls, "should find candidate URLs"
    # rukovodstvo must be first (highest score)
    assert "rukovodstvo" in urls[0], urls


def test_guess_contact_urls_includes_new_paths():
    urls = guess_contact_urls("https://example.com")
    joined = " ".join(urls)
    for tail in ("/press", "/vacancies", "/struktura", "/departments", "/rekvizity"):
        assert tail in joined
