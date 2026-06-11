"""Integration: process_site produces company-only fallback when ФИО absent."""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.pipeline.site_processor import process_site


class _FakeFetcher:
    """Minimal stub: serves a fixed HTML set for any URL on the same host."""

    def __init__(self, home_html: str, pages: dict | None = None):
        self._home = home_html
        self._pages = pages or {}

    async def fetch(self, url: str):
        if url in self._pages:
            return self._pages[url]
        return self._home


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_company_only_record_when_no_person_found():
    """Site with company info but ZERO ФИО must still yield 1 company-only row."""
    html = """<html lang="ru"><head>
      <title>ООО «Ромашка» — поставщик всего</title>
      <meta property="og:site_name" content="ООО Ромашка"/>
    </head><body>
      <footer>
        ООО «Ромашка» ИНН 7701234567 КПП 770101001
        <a href="mailto:info@romashka.ru">info@romashka.ru</a>
        +7 (495) 123-45-67
      </footer>
    </body></html>"""
    fetcher = _FakeFetcher(html)
    result = _run(process_site(fetcher, "https://romashka.ru",
                               mode="fast_start",
                               target_positions=["главный бухгалтер"]))
    assert result["status"] in ("ok", "partial")
    contacts = result["contacts"]
    assert len(contacts) >= 1, "Must emit at least 1 company-only fallback record"
    # first (and likely only) record is company-only
    c = contacts[0]
    assert c["role_category"] == "Компания (без контактного лица)"
    assert c["full_name"] == ""
    assert c["inn"] == "7701234567"
    assert c["company_name"]
    assert c["status"] == "partial"


def test_target_positions_filters_out_non_matching():
    """When target_positions set and no match → still get company-only fallback."""
    html = """<html><body>
      <h1>ООО Ромашка</h1>
      <div class="person">
        <span class="name">Иванов Иван Иванович</span>
        <span class="pos">Разработчик</span>
      </div>
      <footer>ИНН 7701234567</footer>
    </body></html>"""
    fetcher = _FakeFetcher(html)
    result = _run(process_site(fetcher, "https://example.com",
                               mode="fast_start",
                               target_positions=["главный бухгалтер"]))
    # Developer contact filtered out → only company-only row should survive
    contacts = result["contacts"]
    # Could be 0 if no company_info, but we inject ИНН + name so it must be ≥1
    assert len(contacts) >= 1
    # No surviving contact should be "Разработчик"
    for c in contacts:
        assert "разработ" not in (c.get("position_canonical") or "").lower()


def test_inn_collected_across_pages():
    """If ИНН is on /requisites but not on homepage, process_site must still find it."""
    home = """<html><body><h1>Ромашка</h1>
      <a href="/requisites">Реквизиты</a>
    </body></html>"""
    req = """<html><body><footer>ИНН 7701234567 КПП 770101001</footer></body></html>"""
    fetcher = _FakeFetcher(
        home,
        {"https://romashka.ru/requisites": req},
    )
    result = _run(process_site(fetcher, "https://romashka.ru",
                               mode="all_contacts"))
    info = result.get("company_info") or {}
    assert info.get("inn") == "7701234567"
    assert info.get("kpp") == "770101001"


_FX = Path(__file__).resolve().parents[1] / "fixtures" / "html"


def _fx(name: str) -> str:
    return (_FX / f"{name}.html").read_text(encoding="utf-8", errors="ignore")


def test_merge_prefers_opf_name():
    """D1 (письмо п.12): название с ОПФ/кавычками перебивает «голый» заголовок."""
    from backend.pipeline.site_processor import _merge_company_info
    base = {"company_name": "Адрес поставка"}
    _merge_company_info(base, {"company_name": "АО фирма Агрокомплекс"})
    assert base["company_name"] == "АО фирма Агрокомплекс"
    # ОПФ-имя НЕ перебивается «голым»
    base2 = {"company_name": 'ООО «Ромашка»'}
    _merge_company_info(base2, {"company_name": "Контакты"})
    assert base2["company_name"] == 'ООО «Ромашка»'
    # пусто → заполняется любым
    base3 = {"company_name": ""}
    _merge_company_info(base3, {"company_name": "Лачпрофит"})
    assert base3["company_name"] == "Лачпрофит"


def test_company_name_from_root_beats_deep_title_agrokomplex():
    """D1: вход — глубокая страница с мусорным title («Адреса поставки»); корень
    домена несёт юрлицо «АО фирма Агрокомплекс» → оно должно победить."""
    if not (_FX / "agrokomplex_adresa.html").exists():
        import pytest; pytest.skip("agrokomplex fixtures not present")
    fetcher = _FakeFetcher(_fx("agrokomplex_adresa"),
                           {"https://agrokomplex.ru/": _fx("agrokomplex_home")})
    result = _run(process_site(fetcher, "https://agrokomplex.ru/contacts/adresa-postavki/",
                               mode="fast_start"))
    name = (result["company_info"]["company_name"] or "").lower()
    assert "агрокомплекс" in name and name != "адрес поставка"


def test_company_name_from_root_beats_department_title_bti():
    """D1: вход — страница отдела (title «Финансовый отдел»); корень несёт юрлицо
    «АО Бюро…» → должно победить, не «Финансовый отдел»."""
    if not (_FX / "bti_department.html").exists():
        import pytest; pytest.skip("bti fixtures not present")
    fetcher = _FakeFetcher(_fx("bti_department"),
                           {"https://bti.tatarstan.ru/": _fx("bti_home")})
    result = _run(process_site(fetcher, "https://bti.tatarstan.ru/structure.htm?department_id=28181",
                               mode="fast_start"))
    name = (result["company_info"]["company_name"] or "").lower()
    assert "бюро" in name and name != "финансовый отдел"


class _DictFetcher:
    """Отдаёт HTML строго по словарю; для неизвестных URL — None (мёртвая ссылка)."""

    def __init__(self, pages: dict):
        self._pages = pages

    async def fetch(self, url: str):
        return self._pages.get(url)


def test_root_fallback_when_deep_input_dead():
    """F1 (письмо п.7): глубокий вход мёртв (404), корень жив → обрабатываем корень."""
    pages = {
        "https://yolatec.ru/": "<html><body><h1>ООО Йолатек</h1>"
                               "<footer>ИНН 1234567890 info@yolatec.ru</footer></body></html>",
    }
    result = _run(process_site(_DictFetcher(pages),
                               "https://yolatec.ru/contacts.html", mode="fast_start"))
    assert result["status"] in ("ok", "partial")
    assert result["company_info"]["inn"] == "1234567890"


def test_pdf_input_falls_back_to_root():
    """F1: на входе прямая ссылка на .pdf (карточка) → переходим на корень домена."""
    pages = {"https://vzljot.ru/": "<html><body><footer>ИНН 7700000000</footer></body></html>"}
    result = _run(process_site(_DictFetcher(pages),
                               "https://vzljot.ru/files/info.pdf", mode="fast_start"))
    assert result["company_info"]["inn"] == "7700000000"


def test_root_nav_harvested_reaches_requisites():
    """F1/C2: вход — /kontakty без ссылки на реквизиты; корень линкует /o/rekvizity →
    меню корня харвестится, реквизиты доходят (раньше pages_visited=1, ИНН/КПП пусто)."""
    pages = {
        "https://dep.ru/kontakty": "<html><body>Контакты компании</body></html>",
        "https://dep.ru/": '<html><body><nav><a href="/o/rekvizity">Реквизиты</a></nav></body></html>',
        "https://dep.ru/o/rekvizity": "<html><body><footer>ИНН 8601000426 КПП 860101001</footer></body></html>",
    }
    result = _run(process_site(_DictFetcher(pages),
                               "https://dep.ru/kontakty", mode="all_contacts"))
    assert result["company_info"]["inn"] == "8601000426"
    assert result["company_info"]["kpp"] == "860101001"


class _RaisingFetcher:
    """Падает, если fetch вызвали — для проверки, что DNS-отсечка идёт ДО fetch."""

    async def fetch(self, url: str):
        raise AssertionError(f"fetch не должен вызываться для мёртвого домена: {url}")


def test_g2_dns_precheck_skips_dead_domain(monkeypatch):
    """G2: мёртвый домен (NXDOMAIN) → терминальный error без обращения к fetcher."""
    from backend.pipeline import site_processor as sp
    from backend.core.config import settings

    async def _fake_dns(host):
        return "dns_nxdomain"

    monkeypatch.setattr(sp, "_dns_reason", _fake_dns)
    monkeypatch.setattr(settings, "dns_precheck", True)
    result = _run(sp.process_site(_RaisingFetcher(), "https://no-such-domain.invalid",
                                  mode="fast_start"))
    assert result["status"] == "error"
    assert result["error_code"] == "dns_nxdomain"
    assert result["pages_visited"] == 0


class _ErrFetcher:
    """fetch_result отдаёт заданную причину отказа — проверяем проброс в error_code/message."""

    def __init__(self, reason, status=None):
        from backend.fetcher.fetcher import FetchResult
        self._r = FetchResult(None, reason, status)

    async def fetch(self, url: str):
        return self._r.html

    async def fetch_result(self, url: str):
        return self._r


def test_g2_granular_error_code_403():
    """403 → код http_403 и человекочитаемое «вероятно блок по IP»."""
    result = _run(process_site(_ErrFetcher("http_403", 403), "https://blocked.ru",
                               mode="fast_start"))
    assert result["status"] == "error"
    assert result["error_code"] == "http_403"
    assert "403" in result["error_message"]
    assert "блок" in result["error_message"].lower()


def test_g2_granular_error_code_ssl():
    """SSL-ошибка → код ssl_error и сообщение про SSL/TLS."""
    result = _run(process_site(_ErrFetcher("ssl_error"), "https://badssl.ru",
                               mode="fast_start"))
    assert result["error_code"] == "ssl_error"
    assert "SSL" in result["error_message"]


def test_g2_dns_precheck_passes_live_domain(monkeypatch):
    """G2: живой домен (резолв ок) → обычная обработка, fetch вызывается."""
    from backend.pipeline import site_processor as sp
    from backend.core.config import settings

    async def _fake_dns(host):
        return None  # резолвится

    monkeypatch.setattr(sp, "_dns_reason", _fake_dns)
    monkeypatch.setattr(settings, "dns_precheck", True)
    html = "<html><body><footer>ИНН 7701234567 info@live.ru</footer></body></html>"
    result = _run(sp.process_site(_FakeFetcher(html), "https://live.ru", mode="fast_start"))
    assert result["status"] in ("ok", "partial")
    assert result["company_info"]["inn"] == "7701234567"
