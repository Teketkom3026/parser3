"""ФИО normalization via petrovich."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from backend.catalog.loader import get_catalog


@dataclass
class FIO:
    last_name: str = ""
    first_name: str = ""
    patronymic: str = ""
    gender: str = "?"
    full: str = ""
    initials: str = ""
    valid: bool = False


_PETRO = None
_Case = None
_Gender = None


def _get_petrovich():
    global _PETRO, _Case, _Gender
    if _PETRO is None:
        try:
            from petrovich.main import Petrovich
            from petrovich.enums import Case, Gender
            _PETRO = Petrovich()
            _Case = Case
            _Gender = Gender
        except Exception:
            _PETRO = False
    return _PETRO, _Case, _Gender


# Patronymic suffix hints (any case form)
_MALE_PATR = re.compile(r"(ович|евич)(а|у|ем|е)?$", re.I)
_FEMALE_PATR = re.compile(r"(овна|евна|инична|ична)(ы|е|у|ой|ою)?$", re.I)
# Female last names often end with а/я (Иванова, Петрова)
_FEMALE_LAST_SUFFIX = re.compile(r"(ова|ева|ина|ая|ская)$", re.I)

# Unicode letter classes (incl. Cyrillic, Latin, hyphen-compound names)
_WORD = r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\-]*"


def _looks_like_word(w: str) -> bool:
    return bool(re.match(rf"^{_WORD}$", w))


def _load_stopwords() -> set[str]:
    cat = get_catalog()
    stop = set()
    for key in ("roles", "organization_forms", "cities", "geography", "ui_noise"):
        for w in cat.fio_stopwords.get(key, []) or []:
            stop.add(w.lower())
    return stop


_STOPWORDS_CACHE: Optional[set[str]] = None


def _stopwords() -> set[str]:
    global _STOPWORDS_CACHE
    if _STOPWORDS_CACHE is None:
        _STOPWORDS_CACHE = _load_stopwords()
    return _STOPWORDS_CACHE


def _has_stopword(tokens: list[str]) -> bool:
    stop = _stopwords()
    for t in tokens:
        if t.lower() in stop:
            return True
    return False


_EN_STOP_PATTERNS_INTERNAL = [
    re.compile(r"^(about|our|contact|meet|join|the|this|read|show|see)\s+(us|team|form|staff|company|more|all)$", re.I),
    re.compile(r"^\s*page\s+(up|down)\s*$", re.I),
]


def split_fio_raw(raw: str) -> Optional[tuple[str, str, str]]:
    """Simple splitter: "Last First Patronymic" / "First Last" / "I.I. Last".
    Returns (last, first, patronymic) or None.
    """
    if not raw:
        return None
    s = re.sub(r"\s+", " ", raw.strip(" ,.;:\n\t"))
    for pat in _EN_STOP_PATTERNS_INTERNAL:
        if pat.match(s):
            return None
    # strip brackets content
    s = re.sub(r"\s*\([^)]*\)\s*", " ", s).strip()
    if not s:
        return None
    tokens = s.split()
    if len(tokens) < 2 or len(tokens) > 4:
        return None
    for t in tokens:
        if not _looks_like_word(t.replace(".", "")):
            return None
    if _has_stopword(tokens):
        return None

    # Initials pattern: "И.И. Иванов" or "Иванов И.И."
    def is_initials(tok: str) -> bool:
        return bool(re.fullmatch(r"[A-ZА-ЯЁ]\.[A-ZА-ЯЁ]?\.?", tok, re.UNICODE))

    if len(tokens) == 2:
        a, b = tokens
        if is_initials(a):
            return (b, a, "")
        if is_initials(b):
            return (a, b, "")
        # Heuristic: patronymic suffix → last token is patronymic? no
        # Usually "Иван Иванов" or "Иванов Иван": patronymic missing.
        # Try: if second looks like surname pattern (ов/ев/ин/ский) — last first
        if re.search(r"(ов|ев|ин|ский|цкий|енко|юк|ук)$", b, re.I):
            return (b, a, "")
        if re.search(r"(ов|ев|ин|ский|цкий|енко|юк|ук)$", a, re.I):
            return (a, b, "")
        return (a, b, "")  # default: first token = last
    if len(tokens) == 3:
        a, b, c = tokens
        # Classic: "Иванов Иван Иванович"
        if _MALE_PATR.search(c) or _FEMALE_PATR.search(c):
            return (a, b, c)
        # "Иван Иванович Иванов"
        if _MALE_PATR.search(b) or _FEMALE_PATR.search(b):
            return (c, a, b)
        return (a, b, c)
    return None


def _detect_gender(first: str, patronymic: str) -> str:
    if _MALE_PATR.search(patronymic or ""):
        return "М"
    if _FEMALE_PATR.search(patronymic or ""):
        return "Ж"
    if _FEMALE_LAST_SUFFIX.search(""):
        return "Ж"
    # By first name ending
    if first:
        if first.lower().endswith(("а", "я")) and first.lower() not in {"никита", "илья", "кузьма", "фома"}:
            return "Ж"
    return "?"


def normalize_fio(raw: str) -> FIO:
    """Normalize FIO string to nominative case.

    Returns FIO with valid=True only if it parses into 2+ tokens.
    """
    parts = split_fio_raw(raw)
    if not parts:
        return FIO(valid=False)
    last, first, patr = parts
    gender = _detect_gender(first, patr)

    pet, Case, Gender = _get_petrovich()
    if pet:
        try:
            g = Gender.MALE if gender == "М" else (Gender.FEMALE if gender == "Ж" else Gender.ANDROGYNOUS)
            if last:
                last = pet.lastname(last, Case.NOMINATIVE, g)
            if first:
                first = pet.firstname(first, Case.NOMINATIVE, g)
            if patr:
                patr = pet.middlename(patr, Case.NOMINATIVE, g)
        except Exception:
            pass

    # Capitalize compound names
    def _cap(w: str) -> str:
        if not w:
            return ""
        return "-".join(p[:1].upper() + p[1:].lower() for p in w.split("-"))

    last, first, patr = _cap(last), _cap(first), _cap(patr)
    full = " ".join(x for x in (last, first, patr) if x)
    initials = ""
    if first and patr:
        initials = f"{first[:1]}.{patr[:1]}."
    elif first:
        initials = f"{first[:1]}."
    return FIO(
        last_name=last, first_name=first, patronymic=patr,
        gender=gender, full=full, initials=initials, valid=bool(last and first),
    )


# Stop-phrases for English/UI noise
_EN_STOP_PATTERNS = [
    re.compile(r"^(about|our|contact|meet|join|the)\s+(us|team|form|staff|company)$", re.I),
    re.compile(r"^(read|show|see)\s+more$", re.I),
    re.compile(r"^\s*page\s+(up|down)\s*$", re.I),
]


def is_valid_person_name(raw: str) -> bool:
    if not raw:
        return False
    s = raw.strip()
    if len(s) < 3 or len(s) > 120:
        return False
    for pat in _EN_STOP_PATTERNS:
        if pat.match(s):
            return False
    fio = normalize_fio(s)
    return fio.valid
