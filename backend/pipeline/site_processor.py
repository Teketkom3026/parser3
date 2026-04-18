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
    fio = normalize_fio(raw.full_name)
    if not fio.valid:
        # Require at least a name OR (position + contact) to keep
        if not (raw.position_raw and (raw.person_email or raw.person_phone)):
            return None
    pos = normalize_position(raw.position_raw or "")
    sheet = route(pos, fio.full if fio.valid else "")
    domain = domain_from_url(page_url) or company_info.get("domain", "")
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
        "status": "ok",
        "comment": "",
    }
    contact["dedup_key"] = dedup_key(contact)
    return contact


async def process_site(
    fetcher: Fetcher,
    url: str,
    max_pages: int = 4,
) -> Dict:
    """Process one site URL: fetch home page, find contact-related pages, extract contacts."""
    t_start = time.monotonic()
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

    # Gather candidate URLs
    urls_to_visit = [url]
    # Prioritize pages like /team, /контакты
    found_urls = find_contact_urls(home_html, url, max_urls=max_pages * 2)
    # Prioritize "team" / "команда" / "rukovodstvo" links
    def _score(u: str) -> int:
        low = u.lower()
        score = 0
        for kw in ("team", "команд", "rukovodstv", "руководств", "staff", "nasha-komanda"):
            if kw in low:
                score += 10
        for kw in ("contact", "контакт", "kontakty", "about"):
            if kw in low:
                score += 3
        return -score
    found_urls.sort(key=_score)
    for u in found_urls[:max_pages - 1]:
        if u not in urls_to_visit:
            urls_to_visit.append(u)

    # If nothing found, try guesses
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
            raw_contacts = extract_raw_contacts(html, page_url)
            all_raw.extend(raw_contacts)
            for s in extract_social_links(html):
                all_socials.add(s)
        except Exception:
            continue

    # Normalize and dedup
    contacts: List[Dict] = []
    for raw in all_raw:
        c = _normalize_contact(raw, company, raw.page_url or url)
        if c:
            contacts.append(c)

    contacts = dedup(contacts)

    # Attach company-wide socials to a contact record, also keep first social on each
    socials_list = list(all_socials)[:20]
    for c in contacts:
        c["social_links"] = socials_list

    result["contacts"] = contacts
    result["status"] = "ok" if contacts else "partial"
    result["processing_time_ms"] = int((time.monotonic() - t_start) * 1000)
    return result
