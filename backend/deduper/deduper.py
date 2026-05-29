"""Dedup by normalized keys. Priority: email > (name+phone) > (name+site)."""
from __future__ import annotations

import re
from typing import Dict, List


def _norm_name(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _norm_phone(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def dedup_key(c: Dict) -> str:
    name = _norm_name(c.get("full_name") or "")
    site = (c.get("domain") or "").strip().lower()
    if name:
        return f"n:{site}:{name}"
    email = (c.get("person_email") or c.get("company_email") or "").strip().lower()
    phone = _norm_phone(c.get("person_phone") or c.get("company_phone") or "")
    if email:
        return f"e:{site}:{email}"
    if phone:
        return f"p:{site}:{phone}"
    pos = (c.get("position_raw") or "").strip().lower()
    return f"z:{site}:{pos}"


def _fuzzy_key(c: Dict) -> str:
    """Secondary key: last+first+position+site — matches same person without patronymic."""
    last = _norm_name(c.get("last_name") or "")
    first = _norm_name(c.get("first_name") or "")
    site = (c.get("domain") or "").strip().lower()
    pos = _norm_name(c.get("position_canonical") or c.get("position_raw") or "")
    if last and first and pos:
        return f"f:{site}:{last}:{first}:{pos}"
    return ""


def dedup(contacts: List[Dict]) -> List[Dict]:
    seen = {}       # dedup_key → contact
    fuzzy = {}      # _fuzzy_key → contact
    out = []
    for c in contacts:
        k = dedup_key(c)
        if k in seen:
            existing = seen[k]
            if _completeness(c) > _completeness(existing):
                idx = out.index(existing)
                out[idx] = c
                seen[k] = c
                fk = _fuzzy_key(c)
                if fk:
                    fuzzy[fk] = c
            continue

        # Secondary check: same last+first+position+site, different patronymic spelling
        fk = _fuzzy_key(c)
        if fk and fk in fuzzy:
            existing = fuzzy[fk]
            if _completeness(c) > _completeness(existing):
                idx = out.index(existing)
                out[idx] = c
                seen[dedup_key(existing)] = c
                seen[k] = c
                fuzzy[fk] = c
            continue

        seen[k] = c
        if fk:
            fuzzy[fk] = c
        out.append(c)
    return out


def _completeness(c: Dict) -> int:
    score = 0
    if c.get("person_email"):
        score += 100
    if c.get("person_phone") or c.get("company_phone"):
        score += 10
    if c.get("position_canonical") or c.get("position_raw"):
        score += 5
    other = ["full_name", "first_name", "last_name", "patronymic", "company_email", "inn", "kpp"]
    score += sum(1 for k in other if c.get(k))
    return score
