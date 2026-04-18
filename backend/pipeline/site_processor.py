"""The only site processor: fetch → find_pages → extract → normalize → classify → dedup."""
from __future__ import annotations

import time
from typing import Dict, List, Optional
from urllib.parse import urlparse

from backend.classifier.sheet_router import route
from backend.crawler.page_finder import find_contact_urls, guess_contact_urls
from backend.deduper.deduper import dedup, dedup_key
from backend.extractor.company import domain_from_url, extract_company_info
from backend.extractor.contacts import extract_raw_contacts
from backend.fetcher.fetcher import Fetcher
from backend.normalizer.fio import normalize_fio
from backend.normalizer.position import normalize_position
from backend.normalizer.social import extract_social_links


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
        "social_links": [],
        "language": company_info.get("language") or "ru",
        "status": status,
        "comment": comment,
    }
    contact["dedup_key"] = dedup_key(contact)
    return contact


def _merge_company_info(base: Dict, fresh: Dict) -> Dict:
    """Fill in missing fields in `base` from `fresh` (non-destructive merge)."""
    for k in ("company_name", "inn", "kpp", "company_email", "company_phone", "language"):
        if not base.get(k) and fresh.get(k):
            base[k] = fresh[k]
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

    try:
        home_html = await fetcher.fetch(url)
    except Exception as e:
        result["error_code"] = "fetch_exception"
        result["error_message"] = str(e)[:200]
        return result
    if not home_html:
        result["error_code"] = "fetch_failed"
        result["error_message"] = "No HTML returned"
        return result
    result["pages_visited"] = 1

    company = extract_company_info(home_html, url)
    company["domain"] = domain_from_url(url)
    result["company_info"] = company

    # Gather candidate URLs. page_finder already returns list sorted by score DESC
    urls_to_visit = [url]
    found_urls = find_contact_urls(home_html, url, max_urls=max_pages * 2)
    for u in found_urls[:max_pages - 1]:
        if u not in urls_to_visit:
            urls_to_visit.append(u)

    # If nothing found via HTML links, try standard guesses
    if len(urls_to_visit) == 1:
        for u in guess_contact_urls(url)[:max_pages - 1]:
            urls_to_visit.append(u)

    all_raw = []
    all_socials = set()
    for page_url in urls_to_visit:
        try:
            if page_url == url:
                html = home_html
            else:
                html = await fetcher.fetch(page_url)
            if not html:
                continue
            if page_url != url:
                result["pages_visited"] += 1
            # R10+R14: (re-)extract company info on every page and fill missing fields
            fresh_company = extract_company_info(html, page_url)
            _merge_company_info(company, fresh_company)
            raw_contacts = extract_raw_contacts(html, page_url)
            all_raw.extend(raw_contacts)
            for s in extract_social_links(html):
                all_socials.add(s)
        except Exception:
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
