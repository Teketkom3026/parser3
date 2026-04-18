"""Contact extraction from HTML.

Strategy:
1. Look for "cards" — block elements (div/li/article/tr/td) that contain both a position-like text
   and a FIO-like text close together (inside same block or siblings).
2. Fallback: flat-text sliding window — scan lines, detect (position, name, phone/email) triples.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from backend.normalizer.email import extract_emails, split_emails
from backend.normalizer.fio import is_valid_person_name, normalize_fio
from backend.normalizer.phone import extract_phones
from backend.normalizer.social import extract_social_links


# Keywords used as "position marker" for fuzzy line detection
_POSITION_MARKERS = [
    "директор", "бухгалтер", "инженер", "менеджер", "руководитель",
    "начальник", "президент", "специалист", "мастер", "технолог",
    "врач", "юрист", "архитектор", "оператор", "консультант",
    "ректор", "профессор", "советник", "помощник", "казначей",
    "аудитор", "разработчик", "программист", "рекрутер",
    "учредитель", "владелец", "основатель", "секретарь",
    "engineer", "manager", "director", "officer", "developer",
    "accountant", "president", "ceo", "cto", "cfo", "coo", "cio",
    "founder", "owner",
]

_POS_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _POSITION_MARKERS) + r")\b",
    re.IGNORECASE,
)

# FIO candidate: 2..4 words starting uppercase cyrillic or latin
_FIO_CANDIDATE = re.compile(
    r"(?:[A-ZА-ЯЁ][a-zа-яё\-]{1,30})"                 # first word
    r"(?:\s+[A-ZА-ЯЁ][a-zа-яё\-\.]{1,30}){1,3}"      # 1..3 more words
)


@dataclass
class RawContact:
    full_name: str = ""
    position_raw: str = ""
    person_email: str = ""
    person_phone: str = ""
    page_url: str = ""
    source_block: str = ""


def _get_blocks(soup) -> List:
    """Return candidate block elements."""
    sel = ["li", "article", "tr", "div", "section"]
    blocks = []
    for tag in soup.find_all(sel):
        t = tag.get_text(" ", strip=True)
        if 15 <= len(t) <= 2000:
            blocks.append(tag)
    return blocks


def _extract_from_block(tag) -> Optional[RawContact]:
    """Extract contact from a single tag (card-style)."""
    text = tag.get_text("\n", strip=True)
    if not text or len(text) > 2000:
        return None
    pos_match = _POS_RE.search(text)
    if not pos_match:
        return None

    # Find FIO within this block
    fio_candidates = []
    for m in _FIO_CANDIDATE.finditer(text):
        cand = m.group(0)
        if is_valid_person_name(cand):
            fio_candidates.append(cand)
    if not fio_candidates:
        return None

    # Take the first valid FIO; find position line that is closest
    name = fio_candidates[0]

    # Extract position as a cleaner single line — try: the first line containing a position keyword
    position_raw = ""
    for line in text.split("\n"):
        line = line.strip(" \t-–—•·|:")
        if not line:
            continue
        if _POS_RE.search(line) and not is_valid_person_name(line):
            position_raw = line
            break
    if not position_raw:
        # fallback: up to 80 chars around pos_match
        start = max(0, pos_match.start() - 40)
        end = min(len(text), pos_match.end() + 40)
        position_raw = text[start:end].split("\n")[0].strip()

    emails = extract_emails(text)
    phones = extract_phones(text)
    # Classify emails into personal/general
    _, personal = split_emails(emails)
    person_email = personal[0] if personal else (emails[0] if emails else "")

    return RawContact(
        full_name=name,
        position_raw=position_raw,
        person_email=person_email,
        person_phone=phones[0] if phones else "",
        source_block=text[:500],
    )


def _extract_flat_text(html_text: str) -> List[RawContact]:
    """
    Handle the furuno-style pattern:
       Position\nФИО\nТел: ...\nEmail: ...
    Sliding window over non-empty lines.
    """
    lines = [l.strip() for l in re.split(r"\n|<br\s*/?>", html_text) if l.strip()]
    contacts: List[RawContact] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Position line: contains position marker, short enough, not a FIO
        if _POS_RE.search(line) and len(line) < 120 and not is_valid_person_name(line):
            # Look ahead up to 3 lines for FIO
            fio = None
            fio_j = None
            for j in range(i + 1, min(i + 4, len(lines))):
                cand_match = _FIO_CANDIDATE.search(lines[j])
                if cand_match:
                    cand = cand_match.group(0)
                    if is_valid_person_name(cand):
                        fio = cand
                        fio_j = j
                        break
            if fio:
                # Look ahead 4 lines from fio for phone/email
                scope = " \n".join(lines[fio_j:fio_j + 5])
                emails = extract_emails(scope)
                phones = extract_phones(scope)
                _, personal = split_emails(emails)
                person_email = personal[0] if personal else (emails[0] if emails else "")
                contacts.append(RawContact(
                    full_name=fio,
                    position_raw=line.strip(" -–—•·|:"),
                    person_email=person_email,
                    person_phone=phones[0] if phones else "",
                    source_block=" | ".join(lines[i:fio_j + 5]),
                ))
                i = fio_j + 1
                continue
        i += 1
    return contacts


def extract_raw_contacts(html: str, page_url: str = "") -> List[RawContact]:
    from bs4 import BeautifulSoup
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    # Remove script/style/nav/footer for extraction
    for bad in soup(["script", "style", "nav", "header"]):
        bad.decompose()

    text = soup.get_text("\n", strip=True)
    # Main strategy: flat-text window (robust)
    flat = _extract_flat_text(text)

    # Dedup by (name, position_raw) first-pass
    seen = set()
    result: List[RawContact] = []
    for c in flat:
        key = (c.full_name.lower(), c.position_raw.lower())
        if key in seen:
            continue
        seen.add(key)
        c.page_url = page_url
        result.append(c)

    # If nothing — try block-card approach
    if not result:
        for blk in _get_blocks(soup):
            c = _extract_from_block(blk)
            if not c:
                continue
            key = (c.full_name.lower(), c.position_raw.lower())
            if key in seen:
                continue
            seen.add(key)
            c.page_url = page_url
            result.append(c)

    return result
