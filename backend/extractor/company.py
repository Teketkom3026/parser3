"""Extract company metadata from HTML."""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional
from urllib.parse import urlparse

from backend.normalizer.company import (
    clean_company_name,
    extract_inn_from_text,
    extract_kpp_from_text,
    extract_legal_name_from_text,
    extract_ogrn_from_text,
)


def _text(tag) -> str:
    return (tag.get_text(" ", strip=True) if tag else "") or ""


# Headings that signal a requisites section
_REQ_HEADING_RE = re.compile(
    r'\b(реквизиты?|банковские\s+реквизиты?|наши\s+реквизиты?|'
    r'requisites?|legal\s+details?|company\s+details?)\b',
    re.IGNORECASE,
)
# CSS class/id patterns for requisites blocks
_REQ_CLASS_RE = re.compile(r'requisit|rekvizit', re.IGNORECASE)
# Quick INN presence check
_INN_PRESENT_RE = re.compile(r'\bИНН[\s:]*\d{10}', re.IGNORECASE)


def _find_requisites_section(soup) -> Optional[str]:
    """Return text of the requisites section, or None."""
    # 1. Block element with matching class or id
    for tag in soup.find_all(True):
        cls = " ".join(tag.get("class") or [])
        uid = tag.get("id") or ""
        if _REQ_CLASS_RE.search(cls) or _REQ_CLASS_RE.search(uid):
            text = tag.get_text(" ", strip=True)
            if len(text) > 20:
                return text

    # 2. Heading containing requisites keywords → parent section or next sibling
    for heading in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        if _REQ_HEADING_RE.search(heading.get_text(strip=True)):
            parent = heading.find_parent(["div", "section", "article", "main"])
            if parent:
                return parent.get_text(" ", strip=True)
            sib = heading.find_next_sibling()
            if sib:
                return sib.get_text(" ", strip=True)

    # 3. Any short block that directly contains an INN pattern
    for tag in soup.find_all(["p", "div", "td", "li"]):
        text = tag.get_text(" ", strip=True)
        if _INN_PRESENT_RE.search(text) and 20 < len(text) < 2000:
            return text

    return None


def _extract_requisites_from_soup(soup) -> Dict:
    """Extract structured requisites data from an already-parsed soup object."""
    result = {"req_company_name": "", "inn": "", "kpp": "", "ogrn": ""}
    section = _find_requisites_section(soup)
    if not section:
        return result

    inns = extract_inn_from_text(section)
    if inns:
        result["inn"] = inns[0]
    kpps = extract_kpp_from_text(section)
    if kpps:
        result["kpp"] = kpps[0]
    ogrns = extract_ogrn_from_text(section)
    if ogrns:
        result["ogrn"] = ogrns[0]
    result["req_company_name"] = extract_legal_name_from_text(section)

    return result


def extract_company_info(html: str, url: str = "") -> Dict:
    from bs4 import BeautifulSoup

    info = {
        "company_name": "",
        "req_company_name": "",
        "inn": "",
        "kpp": "",
        "ogrn": "",
        "company_email": "",
        "company_phone": "",
        "language": "ru",
    }
    if not html:
        return info

    soup = BeautifulSoup(html, "html.parser")
    html_tag = soup.find("html")
    if html_tag and html_tag.get("lang"):
        info["language"] = html_tag["lang"][:2].lower()

    # --- Requisites section (highest priority for INN/KPP/OGRN and legal name) ---
    req = _extract_requisites_from_soup(soup)
    info["req_company_name"] = req["req_company_name"]
    info["ogrn"] = req["ogrn"]
    # Pre-fill INN/KPP from requisites; full-page scan below fills in only if still empty
    if req["inn"]:
        info["inn"] = req["inn"]
    if req["kpp"]:
        info["kpp"] = req["kpp"]

    # --- Company name candidates (п.3 priority order) ---
    candidates: List[str] = []

    # 1. Legal name from requisites block
    if req["req_company_name"]:
        candidates.append(req["req_company_name"])

    # 2. schema.org/Organization (legalName > name)
    for s in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            data = json.loads(s.string or s.get_text() or "")
        except Exception:
            continue
        nodes = []
        if isinstance(data, dict):
            if "@graph" in data and isinstance(data["@graph"], list):
                nodes = data["@graph"]
            else:
                nodes = [data]
        elif isinstance(data, list):
            nodes = data
        for node in nodes:
            if not isinstance(node, dict):
                continue
            t = node.get("@type") or ""
            if isinstance(t, list):
                t = ",".join(t)
            if "organization" in str(t).lower() or "corporation" in str(t).lower():
                for k in ("legalName", "name"):
                    if node.get(k):
                        candidates.append(str(node[k]))
                        break

    # 3. og:site_name
    og = soup.find("meta", {"property": "og:site_name"})
    if og and og.get("content"):
        candidates.append(og["content"])

    # 4. meta[name=organization]
    mo = soup.find("meta", {"name": "organization"})
    if mo and mo.get("content"):
        candidates.append(mo["content"])

    # 5. <title> — last resort
    t = soup.find("title")
    if t and t.get_text(strip=True):
        candidates.append(t.get_text(strip=True))

    chosen = ""
    for cand in candidates:
        cleaned = clean_company_name(cand)
        if not cleaned:
            continue
        if re.search(r"\b(ООО|ПАО|ОАО|ЗАО|АО|ИП|ФГУП|МУП|ГУП|НКО|АНО)\b", cleaned, re.IGNORECASE):
            chosen = cleaned
            break
        if not chosen:
            chosen = cleaned
    info["company_name"] = chosen

    # --- INN/KPP fallback: full-page scan if not found in requisites section ---
    body_text = soup.get_text(" ", strip=True)
    if not info["inn"]:
        inns = extract_inn_from_text(body_text)
        if inns:
            info["inn"] = inns[0]
    if not info["kpp"]:
        kpps = extract_kpp_from_text(body_text)
        if kpps:
            info["kpp"] = kpps[0]

    # --- Company email / phone ---
    from backend.normalizer.email import extract_emails, split_emails
    from backend.normalizer.phone import extract_phones
    footer = soup.find(["footer"])
    scope = footer.get_text(" ", strip=True) if footer else body_text[-4000:]
    emails = extract_emails(scope)
    gen, _ = split_emails(emails)
    if gen:
        info["company_email"] = gen[0]
    elif emails:
        info["company_email"] = emails[0]

    phones = extract_phones(scope)
    if phones:
        info["company_phone"] = phones[0]

    return info


def domain_from_url(url: str) -> str:
    """Return lowercase host without leading 'www.' prefix.

    NOTE: previously used `host.lstrip("www.")` which is a character-set strip
    and would mangle hosts like 'wwt.ru' → 't.ru'. Now uses proper prefix strip.
    """
    if not url:
        return ""
    try:
        host = urlparse(url).netloc.lower()
        if not host:
            return ""
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""
