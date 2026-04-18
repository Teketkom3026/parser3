"""Extract company metadata from HTML."""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional
from urllib.parse import urlparse

from backend.normalizer.company import clean_company_name, extract_inn_from_text, extract_kpp_from_text


def _text(tag) -> str:
    return (tag.get_text(" ", strip=True) if tag else "") or ""


def extract_company_info(html: str, url: str = "") -> Dict:
    from bs4 import BeautifulSoup

    info = {
        "company_name": "",
        "inn": "",
        "kpp": "",
        "company_email": "",
        "company_phone": "",
        "language": "ru",
    }
    if not html:
        return info

    soup = BeautifulSoup(html, "html.parser")
    # lang
    html_tag = soup.find("html")
    if html_tag and html_tag.get("lang"):
        info["language"] = html_tag["lang"][:2].lower()

    # JSON-LD Organization
    candidates: List[str] = []
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

    # og:site_name
    og = soup.find("meta", {"property": "og:site_name"})
    if og and og.get("content"):
        candidates.append(og["content"])

    # meta organization
    mo = soup.find("meta", {"name": "organization"})
    if mo and mo.get("content"):
        candidates.append(mo["content"])

    # title
    t = soup.find("title")
    if t and t.get_text(strip=True):
        candidates.append(t.get_text(strip=True))

    # Try each candidate — prefer one with ОПФ
    chosen = ""
    for cand in candidates:
        cleaned = clean_company_name(cand)
        if not cleaned:
            continue
        # Prefer candidate containing ОПФ
        if re.search(r"\b(ООО|ПАО|ОАО|ЗАО|АО|ИП|ФГУП|МУП|ГУП|НКО|АНО)\b", cleaned, re.IGNORECASE):
            chosen = cleaned
            break
        if not chosen:
            chosen = cleaned
    info["company_name"] = chosen

    # INN / KPP from full text (incl. footer)
    body_text = soup.get_text(" ", strip=True)
    inns = extract_inn_from_text(body_text)
    if inns:
        info["inn"] = inns[0]
    kpps = extract_kpp_from_text(body_text)
    if kpps:
        info["kpp"] = kpps[0]

    # company phone/email (from footer preferably)
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
    if not url:
        return ""
    try:
        host = urlparse(url).netloc
        return host.lower().lstrip("www.") if host else ""
    except Exception:
        return ""
