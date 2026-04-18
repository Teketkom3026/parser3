"""Email extraction/classification."""
from __future__ import annotations

import re
from typing import List, Tuple


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

_GENERAL_LOCALS = {
    "info", "contact", "contacts", "office", "hello", "mail", "admin",
    "support", "sales", "marketing", "press", "pr", "hr",
    "secretary", "reception", "service", "reklama",
}


def extract_emails(text: str) -> List[str]:
    if not text:
        return []
    seen, out = set(), []
    for m in _EMAIL_RE.finditer(text):
        v = m.group(0).lower()
        # Filter image asset-like strings
        if v.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")):
            continue
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def classify_email(email: str) -> str:
    """Return 'general' or 'personal'."""
    if not email or "@" not in email:
        return "personal"
    local = email.split("@", 1)[0].lower()
    if local in _GENERAL_LOCALS:
        return "general"
    return "personal"


def split_emails(emails: List[str]) -> Tuple[List[str], List[str]]:
    """Returns (general, personal)."""
    general, personal = [], []
    for e in emails:
        (general if classify_email(e) == "general" else personal).append(e)
    return general, personal
