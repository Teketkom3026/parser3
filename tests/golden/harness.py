"""Офлайн golden-харнесс: гоняет РЕАЛЬНЫЙ извлекающий пайплайн на сохранённом HTML.

Повторяет один шаг `backend.pipeline.site_processor.process_site` (обработку ОДНОЙ
страницы) без сети — поэтому замороженная HTML-фикстура даёт ровно те контакты,
что выдал бы прод. Используются те же функции прода (`extract_company_info`,
`extract_raw_contacts`, `_score_url`, `_normalize_contact`, `dedup`), а не их
копии, — иначе тест мерил бы не то, что в проде.

Важно: это извлечение по ОДНОЙ странице (фикстура = одна страница). Многостраничный
обход и слияние company_info между страницами здесь не воспроизводятся — для теста
извлечения по конкретной известной странице это и не нужно.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from backend.crawler.page_finder import _score_url
from backend.deduper.deduper import dedup
from backend.extractor.company import domain_from_url, extract_company_info
from backend.extractor.contacts import extract_raw_contacts
from backend.pipeline.site_processor import _normalize_contact


def run(html: str, url: str, page_score: int | None = None) -> Tuple[List[Dict], Dict]:
    """Вернуть (contacts, company_info) для одной страницы — как в проде.

    page_score: переопределение URL-score. Прод считает `_score_url(page_url)` по
    реальному URL; в фикстурах URL часто известен неточно, а score влияет на полноту
    (на high-score странице ФИО+должность держатся и без личного контакта). Для
    заведомо «руководящих»/контактных фикстур score задаётся явно в cases.yaml.
    """
    company = extract_company_info(html, url)
    company["domain"] = domain_from_url(url)
    score = _score_url(url) if page_score is None else page_score
    raw_list = extract_raw_contacts(html, url, score)
    contacts: List[Dict] = []
    for raw in raw_list:
        c = _normalize_contact(raw, company, url)
        if c:
            contacts.append(c)
    return dedup(contacts), company


def summarize(html: str, url: str, page_score: int | None = None) -> Dict:
    """Стабильная, детерминированная сводка для счётчиков и snapshot-сравнения."""
    contacts, company = run(html, url, page_score)
    people = sorted(
        (
            {
                "full_name": c.get("full_name") or "",
                "position": c.get("position_canonical") or "",
                "sheet": c.get("sheet_name") or "",
                "email": c.get("person_email") or "",
                "phone": c.get("person_phone") or "",
            }
            for c in contacts
            if c.get("full_name")
        ),
        key=lambda r: (r["full_name"], r["position"], r["phone"]),
    )
    return {
        "url": url,
        "n_contacts": len(contacts),   # включая company-only fallback
        "n_people": len(people),       # только записи с распознанным ФИО
        "company": {
            "name": company.get("company_name") or "",
            "inn": company.get("inn") or "",
            "kpp": company.get("kpp") or "",
            "ogrn": company.get("ogrn") or "",
        },
        "people": people,
    }
