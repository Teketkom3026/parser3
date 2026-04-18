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
    email = (c.get("person_email") or c.get("company_email") or "").strip().lower()
    site = (c.get("domain") or "").strip().lower()
    phone = _norm_phone(c.get("person_phone") or c.get("company_phone") or "")
    if email:
        return f"e:{site}:{email}"
    if name and phone:
        return f"np:{site}:{name}:{phone}"
    if name:
        return f"n:{site}:{name}"
    if phone:
        return f"p:{site}:{phone}"
    # Last resort — use position+domain
    pos = (c.get("position_raw") or "").strip().lower()
    return f"z:{site}:{pos}"


def dedup(contacts: List[Dict]) -> List[Dict]:
    seen = {}
    out = []
    for c in contacts:
        k = dedup_key(c)
        if k in seen:
            # Merge: prefer the one with more non-empty fields
            existing = seen[k]
            if _completeness(c) > _completeness(existing):
                # Replace in-place in out
                idx = out.index(existing)
                out[idx] = c
                seen[k] = c
            continue
        seen[k] = c
        out.append(c)
    return out


def _completeness(c: Dict) -> int:
    keys = ["full_name", "first_name", "last_name", "patronymic",
            "position_canonical", "person_email", "person_phone",
            "company_email", "company_phone", "inn", "kpp"]
    return sum(1 for k in keys if c.get(k))
