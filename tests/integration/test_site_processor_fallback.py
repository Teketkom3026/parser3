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
