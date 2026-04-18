"""Company name normalization (ОПФ-aware, pymorphy3-powered)."""
from __future__ import annotations

import re
from typing import Optional

from backend.catalog.loader import get_catalog
from backend.normalizer.morph import get_morph


_OPF_TOKENS = {"ооо", "оао", "пао", "зао", "ао", "ип", "фгуп", "муп", "гуп", "нко", "ано",
               "ltd", "llc", "inc", "gmbh", "corp", "plc"}

_OPF_RE = re.compile(
    r'((?:ООО|ОАО|ПАО|ЗАО|АО|ИП|ФГУП|МУП|ГУП|НКО|АНО)\s*[«"\'`]([^»"\'`]+)[»"\'`])',
    re.IGNORECASE,
)

_QUOTED_RE = re.compile(r'[«"`]([^»"\'`]{2,80})[»"`]')

_SEP_WITH_SPACES = [" | ", " — ", " – ", " · ", " • ", " :: "]


def _strip_html(s: str) -> str:
    s = re.sub(r"&nbsp;", " ", s)
    s = re.sub(r"&quot;", '"', s)
    s = re.sub(r"&amp;", "&", s)
    s = re.sub(r"&laquo;", "«", s)
    s = re.sub(r"&raquo;", "»", s)
    return s


def _to_nominative(phrase: str) -> str:
    morph = get_morph()
    out = []
    tokens = re.split(r"(\s+)", phrase)
    for tok in tokens:
        if not tok.strip():
            out.append(tok)
            continue
        low = tok.lower().strip('«»"\'')
        # Keep quoted tokens unchanged (brand names)
        if re.match(r'^[«"\'`]', tok):
            out.append(tok)
            continue
        if low in _OPF_TOKENS:
            out.append(tok.upper())
            continue
        # Preserve hyphen-compound, numerics, abbrevs
        if not re.search(r"[А-Яа-яЁё]", low):
            out.append(tok)
            continue
        try:
            p = morph.parse(low)[0]
            pos_tag = getattr(p.tag, "POS", None)
            if pos_tag in ("NOUN", "ADJF", "ADJS"):
                infl = p.inflect({"nomn", "sing"})
                if infl and infl.word:
                    w = infl.word
                    # Keep the original capitalization of the first letter
                    if tok[:1].isupper():
                        w = w[:1].upper() + w[1:]
                    out.append(w)
                    continue
            out.append(tok)
        except Exception:
            out.append(tok)
    return "".join(out)


def _split_title(raw: str) -> str:
    """Split title by space-delimited separators only (not by raw `-`)."""
    s = raw.strip()
    for sep in _SEP_WITH_SPACES:
        if sep in s:
            s = s.split(sep)[0].strip()
            break
    return s


def _strip_morph_prefixes(s: str) -> str:
    cat = get_catalog()
    prefixes = [p.lower() for p in cat.companies_stopwords.get("prefixes_morph", []) or []]
    if not prefixes:
        return s
    morph = get_morph()
    tokens = s.split()
    while tokens:
        first = tokens[0]
        # If token has OPF or quote — stop stripping
        if first.lower() in _OPF_TOKENS or re.match(r'^[«"\'`]', first):
            break
        try:
            lemma = morph.parse(first.lower())[0].normal_form
        except Exception:
            lemma = first.lower()
        if lemma in prefixes or first.lower() in prefixes:
            tokens.pop(0)
            continue
        break
    return " ".join(tokens).strip()


def clean_company_name(raw: str) -> str:
    if not raw:
        return ""
    s = _strip_html(raw).strip()
    s = _split_title(s)
    # If OPF + quoted → use that directly
    m = _OPF_RE.search(s)
    if m:
        # Try to capture the full OPF "Name Name" incl. multi-word inside quotes
        return _to_nominative(m.group(1).strip()).strip()

    # Strip junk prefixes (morph-aware)
    s = _strip_morph_prefixes(s)

    # Exact match junk → empty
    cat = get_catalog()
    exact = {x.lower() for x in (cat.companies_stopwords.get("exact_match") or [])}
    if s.lower().strip() in exact:
        return ""

    # If there is a quoted name — keep it
    qm = _QUOTED_RE.search(s)
    if qm:
        brand = qm.group(0)
        # keep any surrounding ОПФ
        return _to_nominative(s).strip()

    s = _to_nominative(s)
    # Limit length
    if len(s) > 120:
        s = s[:120].rsplit(" ", 1)[0]
    # Capitalize first letter
    if s:
        s = s[:1].upper() + s[1:]
    return s.strip()


def extract_inn_from_text(text: str) -> list[str]:
    patterns = [
        r'[Ии][Нн][Нн][\s\xa0\-:.()]*?(\d{10,12})\b',
        r'\bINN[\s\-:]*?(\d{10,12})\b',
        r'\b(\d{10})\s*[/\\]\s*\d{9}\b',
    ]
    seen = set()
    out = []
    for pat in patterns:
        for m in re.finditer(pat, text or "", re.IGNORECASE):
            v = m.group(1)
            if v not in seen and (len(v) == 10 or len(v) == 12):
                seen.add(v)
                out.append(v)
    return out


def extract_kpp_from_text(text: str) -> list[str]:
    seen = set()
    out = []
    for m in re.finditer(r'[Кк][Пп][Пп][\s\xa0\-:.()]*?(\d{9})\b', text or ""):
        v = m.group(1)
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out
