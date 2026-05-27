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

_TRANSLIT: dict[str, str] = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e',
    'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'i', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch',
    'ъ': '',  'ы': 'y',  'ь': '',  'э': 'e', 'ю': 'yu', 'я': 'ya',
}


def _translit(s: str) -> str:
    return "".join(_TRANSLIT.get(c, c) for c in s.lower())


def _norm_local(s: str) -> str:
    """Strip separators (. - _) from local-part for bare comparison."""
    return re.sub(r"[.\-_]", "", s.lower())


def _matches_fio(local: str, full_name: str) -> bool:
    """Return True if local-part of email encodes any part of the FIO.

    Checks (in order):
      1. Full transliterated surname or first name present in local-part.
      2. initial+surname or surname+initial pattern.
      3. surname+firstname or firstname+surname (concatenated).
    """
    parts = [p for p in full_name.strip().split() if p]
    if not parts:
        return False

    trans = [_translit(p) for p in parts]
    local_clean = _norm_local(local)

    # 1. Any transliterated part appears in local-part (or equals it)
    for t in trans:
        if len(t) < 3:
            continue
        if local_clean == t or t in local_clean:
            return True

    # 2. Initials-based patterns — need at least surname + first name
    if len(trans) >= 2:
        surname, first = trans[0], trans[1]
        if len(surname) >= 3 and len(first) >= 1:
            # i.surname → isurname
            if local_clean == first[0] + surname:
                return True
            # surname.i → surnamei
            if local_clean == surname + first[0]:
                return True
        # 3. Full concatenation surname+first or first+surname
        if len(surname) >= 3 and len(first) >= 3:
            if local_clean in (surname + first, first + surname):
                return True

    return False


def extract_emails(text: str) -> List[str]:
    if not text:
        return []
    seen, out = set(), []
    for m in _EMAIL_RE.finditer(text):
        v = m.group(0).lower()
        if v.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")):
            continue
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def classify_email(email: str, full_name: str = "") -> str:
    """Return 'general' or 'personal'.

    With FIO: personal only when local-part encodes the person's name via
    transliteration. Anything else (including role words) → general.
    Without FIO: fall back to GENERAL_LOCALS heuristic only.
    """
    if not email or "@" not in email:
        return "personal"
    local = email.split("@", 1)[0].lower()

    if local in _GENERAL_LOCALS:
        return "general"

    if full_name:
        return "personal" if _matches_fio(local, full_name) else "general"

    return "personal"


def split_emails(emails: List[str], full_name: str = "") -> Tuple[List[str], List[str]]:
    """Returns (general, personal)."""
    general, personal = [], []
    for e in emails:
        (general if classify_email(e, full_name) == "general" else personal).append(e)
    return general, personal
