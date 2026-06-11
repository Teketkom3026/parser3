"""The only site processor: fetch → find_pages → extract → normalize → classify → dedup."""
from __future__ import annotations

import asyncio
import re
import socket
import time
from typing import Dict, List, Optional
from urllib.parse import urlparse

from backend.core.config import settings
from backend.classifier.sheet_router import route
from backend.crawler.page_finder import _score_url, find_contact_urls, guess_contact_urls
from backend.deduper.deduper import dedup, dedup_key
from backend.extractor.company import domain_from_url, extract_company_info
from backend.extractor.contacts import extract_raw_contacts
from backend.fetcher.fetcher import Fetcher
from backend.normalizer.fio import normalize_fio
from backend.normalizer.position import normalize_position
from backend.normalizer.social import extract_social_links
from backend.core.logging import get_logger

log = get_logger("site_processor")


def _normalize_contact(raw, company_info: Dict, page_url: str) -> Optional[Dict]:
    """Normalize a raw extracted contact.

    R1 policy (ula-dacko priority):
      * If ФИО is valid  → keep, even without email/phone.
      * If ФИО invalid BUT position_raw is present → keep as position-only record.
      * Otherwise → drop.
    """
    fio = normalize_fio(raw.full_name)
    has_name = fio.valid
    has_position = bool((raw.position_raw or "").strip())
    has_contact = bool(raw.person_email or raw.person_phone)
    if not has_name and not has_position:
        return None
    pos = normalize_position(raw.position_raw or "")
    sheet = route(pos, fio.full if fio.valid else "")
    domain = domain_from_url(page_url) or company_info.get("domain", "")
    status = "ok"
    comment = ""
    if not has_name and has_position:
        status = "partial"
        comment = "Должность без ФИО"
    # Last name ending for declensions (R15): keep last 2–3 chars
    last = fio.last_name or ""
    ending = last[-3:] if len(last) >= 3 else last[-2:] if len(last) >= 2 else ""
    contact = {
        "domain": domain,
        "page_url": page_url,
        "company_name": company_info.get("company_name") or "",
        "company_email": company_info.get("company_email") or "",
        "company_phone": company_info.get("company_phone") or "",
        "full_name": fio.full if fio.valid else "",
        "last_name": fio.last_name,
        "first_name": fio.first_name,
        "patronymic": fio.patronymic,
        "ending": ending,
        "gender": fio.gender,
        "position_raw": raw.position_raw or "",
        "position_canonical": pos.canonical,
        "role_category": pos.category,
        "matched_entry_id": pos.matched_id,
        "norm_method": pos.method,
        "sheet_name": sheet,
        "person_email": raw.person_email,
        "person_phone": raw.person_phone,
        "inn": company_info.get("inn") or "",
        "kpp": company_info.get("kpp") or "",
        "ogrn": company_info.get("ogrn") or "",
        "req_company_name": company_info.get("req_company_name") or "",
        "social_links": [],
        "language": company_info.get("language") or "ru",
        "status": status,
        "comment": comment,
    }
    contact["dedup_key"] = dedup_key(contact)
    return contact


# D1: маркер юрлица — ОПФ (АО/ООО/…) или кавычки бренда. Название С маркером
# авторитетнее «голого» заголовка глубокой страницы («Адреса поставки»/«Финансовый отдел»).
_OPF_MARKER_RE = re.compile(
    r'\b(ООО|ОАО|ПАО|ЗАО|АО|ИП|ФГУП|МУП|ГУП|НКО|АНО)\b|[«"]', re.IGNORECASE
)


def _name_has_opf(name: str) -> bool:
    return bool(_OPF_MARKER_RE.search(name or ""))


# F1: расширения, которые точно не HTML (вход-ссылка на файл, не страницу).
# Частый кейс из писем — прямые .pdf (карточки организаций).
_NONHTML_EXT = {
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "rtf", "odt",
    "zip", "rar", "7z", "csv", "jpg", "jpeg", "png", "gif", "svg", "webp",
    "mp4", "mp3", "avi", "mov", "exe",
}


def _is_nonhtml_url(u: str) -> bool:
    last = urlparse(u).path.rsplit("/", 1)[-1]
    if "." not in last:
        return False
    return last.rsplit(".", 1)[-1].lower() in _NONHTML_EXT


def _merge_company_info(base: Dict, fresh: Dict) -> Dict:
    """Fill in missing fields in `base` from `fresh` (non-destructive merge).

    company_name (D1): помимо «пусто → заполнить» даём апгрейд — название с маркером
    ОПФ/кавычек (АО «…») перебивает уже стоящее БЕЗ маркера. Иначе company_name
    фиксируется с первой обработанной страницы, а у глубоких страниц title — тема
    страницы («Адреса поставки», «Финансовый отдел»), не юрлицо (письмо п.12).
    """
    for k in ("inn", "kpp", "ogrn", "req_company_name", "company_email", "company_phone", "language"):
        if not base.get(k) and fresh.get(k):
            base[k] = fresh[k]
    bn, fn = base.get("company_name"), fresh.get("company_name")
    if fn and (not bn or (_name_has_opf(fn) and not _name_has_opf(bn))):
        base["company_name"] = fn
    return base


def _make_company_only_record(company: Dict, url: str) -> Dict:
    """Build a single 'company-only' record when no personal ФИО found (R17/R18)."""
    domain = company.get("domain") or domain_from_url(url)
    rec = {
        "domain": domain,
        "page_url": url,
        "company_name": company.get("company_name") or "",
        "company_email": company.get("company_email") or "",
        "company_phone": company.get("company_phone") or "",
        "inn": company.get("inn") or "",
        "kpp": company.get("kpp") or "",
        "ogrn": company.get("ogrn") or "",
        "req_company_name": company.get("req_company_name") or "",
        "full_name": "",
        "last_name": "",
        "first_name": "",
        "patronymic": "",
        "ending": "",
        "gender": "",
        "position_raw": "",
        "position_canonical": "",
        "role_category": "Компания (без контактного лица)",
        "matched_entry_id": None,
        "norm_method": "empty",
        "sheet_name": "Остальные",
        "person_email": "",
        "person_phone": "",
        "social_links": [],
        "language": company.get("language", "ru"),
        "status": "partial",
        "comment": "Компания без персональных ФИО",
    }
    rec["dedup_key"] = dedup_key(rec)
    return rec


def _has_any_company_data(company: Dict) -> bool:
    """company_info is 'non-empty' if at least one key field is set."""
    for k in ("company_name", "company_email", "company_phone", "inn"):
        if (company.get(k) or "").strip():
            return True
    return False


def _matches_target_positions(contact: Dict, target_positions: List[str]) -> bool:
    """Return True if contact's position_raw or position_canonical contains any target keyword (case-insensitive)."""
    if not target_positions:
        return True
    canon = (contact.get("position_canonical") or "").lower()
    raw = (contact.get("position_raw") or "").lower()
    for tp in target_positions:
        needle = (tp or "").strip().lower()
        if not needle:
            continue
        if needle in canon or needle in raw:
            return True
    return False


async def _dns_reason(host: str) -> Optional[str]:
    """G2: вернуть код проблемы DNS или None, если host резолвится.

    None      — имя резолвится (или проверить нельзя — не блокируем, пусть fetch решит).
    'dns_nxdomain' — getaddrinfo сказал «нет такого хоста» (мёртвый домен).
    'dns_timeout'  — резолвер завис дольше dns_timeout_sec.
    Один резолв на host в начале process_site экономит ~25с слота браузера на мёртвых
    доменах (ERR_NAME_NOT_RESOLVED — основная масса «No HTML» в прогонах, см. HANDOFF).
    """
    if not host:
        return None
    loop = asyncio.get_event_loop()
    try:
        await asyncio.wait_for(
            loop.getaddrinfo(host, None, type=socket.SOCK_STREAM),
            timeout=settings.dns_timeout_sec,
        )
        return None
    except asyncio.TimeoutError:
        return "dns_timeout"
    except socket.gaierror:
        return "dns_nxdomain"
    except Exception:
        return None  # неизвестная ошибка резолва — не блокируем, отдаём fetch


async def process_site(
    fetcher: Fetcher,
    url: str,
    *,
    mode: str = "all_contacts",
    target_positions: Optional[List[str]] = None,
    max_pages: Optional[int] = None,
) -> Dict:
    """Process one site URL: fetch home page, find contact-related pages, extract contacts.

    Parameters
    ----------
    mode: ``"fast_start"`` or ``"all_contacts"``. Controls default depth of crawl.
    target_positions: optional list of keywords. If set, contacts are kept only
        when their raw/canonical position contains one of the keywords. Company-only
        fallback records are always kept (R17).
    max_pages: explicit override; if ``None`` derived from mode
        (``fast_start`` → 6, ``all_contacts`` → 15).
    """
    t_start = time.monotonic()
    if max_pages is None:
        max_pages = 6 if mode == "fast_start" else 15
    target_positions = target_positions or []

    result = {
        "url": url,
        "status": "error",
        "error_code": "",
        "error_message": "",
        "pages_visited": 0,
        "contacts": [],
        "company_info": {},
    }

    # Ensure URL has scheme
    if not url.startswith(("http://", "https://")):
        url = "https://" + url.strip()

    _p = urlparse(url)
    root = f"{_p.scheme}://{_p.netloc}/"
    input_is_deep = bool(_p.path.strip("/")) or bool(_p.query)

    # G2: DNS-отсечка до любого fetch/браузера. Глубокий вход и корень делят один host,
    # поэтому одной проверки достаточно. Мёртвый домен → сразу терминальный error.
    if settings.dns_precheck:
        dns_bad = await _dns_reason(_p.hostname)
        if dns_bad:
            log.info("dns_precheck_failed", url=url, host=_p.hostname, reason=dns_bad)
            result["error_code"] = dns_bad
            result["error_message"] = f"DNS resolve failed ({dns_bad}): {_p.hostname}"
            return result

    # Fetch the entry page. F1 (письмо п.7): вход часто НЕ на первом уровне — глубокая
    # ссылка протухла (404), это PDF (карточка организации) или иной не-HTML. В таких
    # случаях пробуем КОРЕНЬ домена: сам сайт обычно жив на первом уровне.
    async def _safe_fetch(u: str) -> Optional[str]:
        try:
            return await fetcher.fetch(u)
        except Exception as e:
            log.info("fetch_exception", url=u, error=str(e)[:200])
            return None

    home_html: Optional[str] = None
    if not _is_nonhtml_url(url):
        home_html = await _safe_fetch(url)
    if not home_html and input_is_deep and root != url:
        root_html = await _safe_fetch(root)
        if root_html:
            log.info("entry_root_fallback", input=url, root=root)
            url = root
            _p = urlparse(url)
            input_is_deep = False
            home_html = root_html

    if not home_html:
        result["error_code"] = "fetch_failed"
        result["error_message"] = "No HTML returned"
        return result
    result["pages_visited"] = 1

    company = extract_company_info(home_html, url)
    company["domain"] = domain_from_url(url)
    result["company_info"] = company

    # Build crawl frontier. F1/D1/C2: для глубокого входа дополнительно тянем КОРЕНЬ
    # домена и харвестим ссылки И со входной страницы, И с корня — меню homepage
    # линкует стандартные разделы (/rekvizity, /rukovodstvo), которых нет на глубокой
    # входной странице. find_contact_urls сам корень не находит («/» не матчит keyword).
    prefetched: Dict[str, str] = {url: home_html}
    urls_to_visit = [url]
    if input_is_deep:
        root_html = await _safe_fetch(root)
        if root_html:
            prefetched[root] = root_html
            urls_to_visit.append(root)

    candidates: List[str] = []
    for src_url, src_html in prefetched.items():
        candidates.extend(find_contact_urls(src_html, src_url, max_urls=max_pages * 2))
    seen = set(urls_to_visit)
    for u in sorted(set(candidates), key=lambda x: -_score_url(x)):
        if len(urls_to_visit) >= max_pages:
            break
        if u not in seen:
            urls_to_visit.append(u)
            seen.add(u)

    # If nothing found via HTML links, try standard guesses
    if len(urls_to_visit) == 1:
        for u in guess_contact_urls(url)[:max_pages - 1]:
            urls_to_visit.append(u)

    all_raw = []
    all_socials = set()
    for page_url in urls_to_visit:
        try:
            html = prefetched.get(page_url)
            if html is None:
                html = await _safe_fetch(page_url)
            if not html:
                continue
            if page_url != url:
                result["pages_visited"] += 1
            # R10+R14: (re-)extract company info on every page and fill missing fields
            fresh_company = extract_company_info(html, page_url)
            _merge_company_info(company, fresh_company)
            # П.1.1/П.1.2: pass URL score so high-score leadership/contacts pages
            # keep ФИО+должность contacts even without a personal phone/email.
            raw_contacts = extract_raw_contacts(html, page_url, _score_url(page_url))
            all_raw.extend(raw_contacts)
            for s in extract_social_links(html):
                all_socials.add(s)
        except Exception as e:
            # Не глушим молча: краш extract_company_info/extract_raw_contacts на
            # отдельной странице раньше пропадал бесследно. Страницу пропускаем,
            # но факт фиксируем (warning — такие исключения редки и указывают на баг).
            log.warning("page_processing_failed", url=page_url, error=str(e)[:200])
            continue

    # Ensure domain preserved after merges
    company["domain"] = company.get("domain") or domain_from_url(url)
    result["company_info"] = company

    # Normalize and dedup
    contacts: List[Dict] = []
    for raw in all_raw:
        c = _normalize_contact(raw, company, raw.page_url or url)
        if c:
            contacts.append(c)

    contacts = dedup(contacts)

    # Attach company-wide socials
    socials_list = list(all_socials)[:20]
    for c in contacts:
        c["social_links"] = socials_list

    # target_positions filter (personal contacts only; company-only added below)
    if target_positions:
        contacts = [c for c in contacts if _matches_target_positions(c, target_positions)]

    # R17/R18: if no personal contacts survived AND we have any company info —
    # emit one "company-only" fallback record so the table is never empty.
    if not contacts and _has_any_company_data(company):
        contacts.append(_make_company_only_record(company, url))

    result["contacts"] = contacts
    # status: 'ok' if we have any row (incl. company-only), 'partial' otherwise
    result["status"] = "ok" if contacts else "partial"
    result["processing_time_ms"] = int((time.monotonic() - t_start) * 1000)
    return result
