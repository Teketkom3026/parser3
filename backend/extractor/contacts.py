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
    "заведующий", "заведующая", "глава", "заместитель",
    "engineer", "manager", "director", "officer", "developer",
    "accountant", "president", "ceo", "cto", "cfo", "coo", "cio",
    "founder", "owner",
]

# Prefix match (\w*) so inflected forms are detected too: "директора"/"директору"
# (genitive/dative). Without this, "Заместитель директора" was not recognised as a
# position line and every replacement-head/deputy row was dropped (П.1.1/П.1.2).
_POS_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in _POSITION_MARKERS) + r")\w*",
    re.IGNORECASE,
)

# FIO candidate: 2..4 words starting uppercase cyrillic or latin
_FIO_CANDIDATE = re.compile(
    r"(?:[A-ZА-ЯЁ][a-zа-яё\-]{1,30})"                 # first word
    r"(?:\s+[A-ZА-ЯЁ][a-zа-яё\-\.]{1,30}){1,3}"      # 1..3 more words
)

# Strip emails before FIO detection to prevent "Фамилия ceo@domain.com" sticking
_EMAIL_STRIP = re.compile(r"\S+@\S+")

# П.1.1/П.1.2: on a clearly-identified leadership/team/contacts page (high URL
# score from page_finder._score_url) we keep a valid ФИО+должность even without a
# personal phone/email. The client reported losing real managers whose only phone
# is the company-wide one listed elsewhere on the page. On low-score pages (home,
# news, about) the contact-detail requirement still filters testimonials/quotes.
_HIGH_URL_SCORE = 5


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


def _extract_from_block(tag, page_score: int = 0) -> Optional[RawContact]:
    """Extract contact from a single tag (card-style)."""
    text = _merge_tag_split_lines(tag.get_text("\n", strip=True))
    if not text or len(text) > 2000:
        return None
    pos_match = _POS_RE.search(text)
    if not pos_match:
        return None

    # Find FIO within this block (strip emails first to prevent sticking)
    fio_candidates = []
    for m in _FIO_CANDIDATE.finditer(_EMAIL_STRIP.sub(" ", text)):
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
    # Require at least one contact detail — filters testimonial/vacancy blocks
    # that happen to contain a name and a position keyword but are not real contacts.
    # Exception (П.1.1/П.1.2): on a high-score leadership/contacts page keep the
    # name+position even without a personal contact detail.
    if not emails and not phones and page_score < _HIGH_URL_SCORE:
        return None
    # Classify emails into personal/general
    _, personal = split_emails(emails, full_name=name)
    person_email = personal[0] if personal else ""

    return RawContact(
        full_name=name,
        position_raw=position_raw,
        person_email=person_email,
        person_phone=phones[0] if phones else "",
        source_block=text[:500],
    )


def _merge_tag_split_lines(text: str) -> str:
    """Merge single-letter artefact lines with the following line.

    BeautifulSoup get_text(separator="\\n", strip=True) inserts a newline between
    every NavigableString, including inline tags. A pattern like
    <b>Г</b>енеральный директор produces "Г\\nенеральный директор".
    When the previous accumulated line is exactly one alpha character and the
    next line starts with a lowercase letter, they belong to the same word.
    """
    lines = text.split("\n")
    out: list[str] = []
    for line in lines:
        if (out
                and out[-1]
                and len(out[-1].strip()) == 1
                and out[-1].strip().isalpha()
                and line
                and line[0].islower()):
            out[-1] = out[-1].strip() + line
        else:
            out.append(line)
    return "\n".join(out)


def _is_pos_line(line: str) -> bool:
    return bool(_POS_RE.search(line) and len(line) < 120 and not is_valid_person_name(line))


def _scan_for_fio(lines: List[str], indices) -> tuple:
    """Scan lines at given indices for the first valid FIO. Returns (fio, j) or (None, None)."""
    for j in indices:
        if j < 0 or j >= len(lines):
            continue
        if _is_pos_line(lines[j]):
            break  # card boundary — another position line
        cand_match = _FIO_CANDIDATE.search(_EMAIL_STRIP.sub(" ", lines[j]))
        if cand_match and is_valid_person_name(cand_match.group(0)):
            return cand_match.group(0), j
    return None, None


def _extract_flat_text(html_text: str, page_score: int = 0) -> List[RawContact]:
    """Sliding window over non-empty lines. Handles both position→FIO and FIO→position order.

    Forward scan is limited to 2 lines to avoid crossing card boundaries.
    When forward scan finds nothing, a backward scan (up to 3 lines) is tried —
    this covers Drupal/CMS layouts where the name appears above the job title.
    """
    html_text = _merge_tag_split_lines(html_text)
    lines = [l.strip() for l in re.split(r"\n|<br\s*/?>", html_text) if l.strip()]
    contacts: List[RawContact] = []
    used: set[int] = set()  # FIO line indices already attached to a position
    i = 0
    while i < len(lines):
        line = lines[i]
        if _is_pos_line(line):
            # Find the nearest ФИО around this position line. Both layouts occur:
            #   ФИО → должность (name above title)  → look backward
            #   должность → ФИО (title above name)  → look forward
            # Take the CLOSEST candidate; on a tie prefer the preceding name (the
            # dominant Russian layout). Forward-first used to grab the NEXT card's
            # ФИО in «ФИО\nдолжность\nФИО\nдолжность» tables — mismatching pairs and
            # dropping one person per pair (П.1.1/П.1.2).
            back_fio, back_j = _scan_for_fio(lines, range(i - 1, max(i - 4, -1), -1))
            fwd_fio, fwd_j = _scan_for_fio(lines, range(i + 1, min(i + 3, len(lines))))
            if back_fio is not None and (fwd_fio is None or (i - back_j) <= (fwd_j - i)):
                fio, fio_j = back_fio, back_j
            else:
                fio, fio_j = fwd_fio, fwd_j

            if fio is not None:
                lo, hi = min(fio_j, i), max(fio_j, i)
                scope = " \n".join(lines[lo:hi + 5])
                emails = extract_emails(scope)
                phones = extract_phones(scope)
                # П.1.1/П.1.2: keep name+position without contact detail on a
                # high-score leadership/contacts page; otherwise drop (citation/vacancy).
                if not emails and not phones and page_score < _HIGH_URL_SCORE:
                    i = hi + 1
                    continue  # citation/vacancy block — no contact details
                _, personal = split_emails(emails, full_name=fio)
                person_email = personal[0] if personal else ""
                contacts.append(RawContact(
                    full_name=fio,
                    position_raw=line.strip(" -–—•·|:"),
                    person_email=person_email,
                    person_phone=phones[0] if phones else "",
                    source_block=" | ".join(lines[lo:hi + 5]),
                ))
                used.add(fio_j)
                i = hi + 1
                continue
        i += 1

    # П.1.1/П.1.2: second pass for name-only rows on high-score pages — an ФИО line
    # with a phone/email nearby but no own position line. Covers district-hotline
    # tables that list «ответственное лицо» as just a name + phone. Gated on page
    # score + is_valid_person_name + an adjacent contact detail to avoid capturing
    # stray capitalized phrases on ordinary pages.
    if page_score >= _HIGH_URL_SCORE:
        for j, line in enumerate(lines):
            if j in used or _is_pos_line(line):
                continue
            m = _FIO_CANDIDATE.search(_EMAIL_STRIP.sub(" ", line))
            if not (m and is_valid_person_name(m.group(0))):
                continue
            scope = " \n".join(lines[max(0, j - 1):j + 3])
            emails = extract_emails(scope)
            phones = extract_phones(scope)
            if not emails and not phones:
                continue
            _, personal = split_emails(emails, full_name=m.group(0))
            contacts.append(RawContact(
                full_name=m.group(0),
                position_raw="",
                person_email=personal[0] if personal else "",
                person_phone=phones[0] if phones else "",
                source_block=scope[:500],
            ))
            used.add(j)
    return contacts


def extract_raw_contacts(html: str, page_url: str = "", page_score: int = 0) -> List[RawContact]:
    from bs4 import BeautifulSoup
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    # Remove script/style/nav/footer for extraction
    for bad in soup(["script", "style", "nav", "header"]):
        bad.decompose()

    text = soup.get_text("\n", strip=True)
    # Main strategy: flat-text window (robust)
    flat = _extract_flat_text(text, page_score)

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
            c = _extract_from_block(blk, page_score)
            if not c:
                continue
            key = (c.full_name.lower(), c.position_raw.lower())
            if key in seen:
                continue
            seen.add(key)
            c.page_url = page_url
            result.append(c)

    return result
