"""ФИО normalization via petrovich."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from backend.catalog.loader import get_catalog
from backend.normalizer.morph import get_morph


@dataclass
class FIO:
    last_name: str = ""
    first_name: str = ""
    patronymic: str = ""
    gender: str = "?"
    full: str = ""
    initials: str = ""
    ending: str = ""  # R15: last 2–3 chars of last_name (for declension hinting)
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


_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
_LATIN_ONLY_RE = re.compile(r"^[A-Za-z\-\.]+$")

# pymorphy3 grammemes that mark a token as a personal-name part.
_NAME_GRAMMEMES = frozenset({"Name", "Surn", "Patr"})


def _has_name_morph(tokens: list[str]) -> bool:
    """True if any Cyrillic token is tagged Name/Surn/Patr by pymorphy.

    Real ФИО carry at least one such token (even when the surname is unknown,
    the given name or patronymic is recognised). Institutional phrases like
    «Российской Федерации» / «Администрации Санкт-» parse as adjective+noun and
    return False — used to drop them as garbage ФИО (П.4).
    """
    morph = get_morph()
    for t in tokens:
        w = t.strip(".")
        if not _CYRILLIC_RE.search(w):
            continue
        try:
            for p in morph.parse(w)[:5]:
                if _NAME_GRAMMEMES & set(p.tag.grammemes):
                    return True
        except Exception:
            continue
    return False

# Common English first names (extend if needed). If a fully-Latin candidate
# does not include one of these, we reject it as not-a-person-name.
_EN_FIRST_NAMES = {
    "john", "jane", "michael", "david", "james", "robert", "william", "thomas", "sarah",
    "emily", "anna", "mary", "chris", "daniel", "andrew", "paul", "peter", "steve",
    "steven", "alex", "mark", "matt", "matthew", "jacob", "alexander", "sergei",
    "sergey", "ivan", "alexey", "dmitry", "vladimir", "yuri", "oleg", "igor",
    "nikolay", "viktor", "viktoria", "olga", "elena", "natalia", "tatiana",
}


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
    # П.4: two identical consecutive words are never a real ФИО («Переговоры Переговоры»).
    for a, b in zip(tokens, tokens[1:]):
        if a.lower() == b.lower():
            return None
    # П.4: a Cyrillic candidate must carry a real name morphology signal — at least
    # one token tagged Name/Surn/Patr (or an initials token). Institutional phrases
    # («Российской Федерации», «Администрации Санкт-», «Образовательного учреждения»)
    # parse as adjective+noun and are dropped here, instead of an endless stop-list.
    if any(_CYRILLIC_RE.search(t) for t in tokens):
        has_initials_tok = any(re.fullmatch(r"[A-ZА-ЯЁ]\.[A-ZА-ЯЁ]?\.?", t) for t in tokens)
        if not has_initials_tok and not _has_name_morph(tokens):
            return None
    # Reject Latin-only candidates without a recognizable English/transliterated
    # first name — fixes "Mobile Inform Group" being parsed as ФИО (R11/R9).
    if not any(_CYRILLIC_RE.search(t) for t in tokens):
        latin_only = all(_LATIN_ONLY_RE.match(t) for t in tokens)
        if latin_only:
            if not any(t.lower().strip(".") in _EN_FIRST_NAMES for t in tokens):
                return None

    # Reject Cyrillic-but-not-Russian-name candidates (e.g. "Адванст Мобилити Солюшинз",
    # "Глобал Эксперт Сервис") — typical Cyrillic transliterations of company brands.
    # Heuristic: must have at least one of
    #   * patronymic suffix (Иванович, Петровна, …) — strongest signal
    #   * Russian surname suffix (ов/ев/ин/ский/ая/енко/юк/…)
    #   * initials (И.И.)
    # If a 3+ token candidate has none of these and is pure Cyrillic, drop.
    if len(tokens) >= 3:
        has_patronymic = any(_MALE_PATR.search(t) or _FEMALE_PATR.search(t) for t in tokens)
        has_surname_suffix = any(
            re.search(r"(ов|ев|ин|ский|цкий|енко|юк|ук|ая|ская|ова|ева|ина|ёв|ёва)$", t, re.I)
            for t in tokens
        )
        has_initials_tok = any(re.fullmatch(r"[A-ZА-ЯЁ]\.[A-ZА-ЯЁ]?\.?", t) for t in tokens)
        if not (has_patronymic or has_surname_suffix or has_initials_tok):
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
    ending = ""
    if last:
        if len(last) >= 3:
            ending = last[-3:]
        elif len(last) >= 2:
            ending = last[-2:]
    return FIO(
        last_name=last, first_name=first, patronymic=patr,
        gender=gender, full=full, initials=initials, ending=ending,
        valid=bool(last and first),
    )


# Stop-phrases for English/UI noise
_EN_STOP_PATTERNS = [
    re.compile(r"^(about|our|contact|meet|join|the)\s+(us|team|form|staff|company)$", re.I),
    re.compile(r"^(read|show|see)\s+more$", re.I),
    re.compile(r"^\s*page\s+(up|down)\s*$", re.I),
]


_FULL_PATR_RE = re.compile(r"(вич|тич|вна|чна)$", re.I)


def make_dative_fields(last: str, first: str, patr: str, gender: str) -> tuple[str, str]:
    """Return (surname_io_dative, gender_ending) for full ФИО with Russian patronymic.

    Condition: all three parts present AND patronymic ends with вич/тич/вна/чна.
    Returns ("", "") if not met.
    """
    if not (last and first and patr):
        return "", ""
    if not _FULL_PATR_RE.search(patr):
        return "", ""

    p_low = patr.lower()
    if p_low.endswith(("вич", "тич")):
        ending = "ый"
    elif p_low.endswith(("вна", "чна")):
        ending = "ая"
    else:
        ending = ""

    pet, Case, Gender = _get_petrovich()
    if not pet:
        return "", ending
    try:
        g = Gender.MALE if gender == "М" else Gender.FEMALE
        last_dat = pet.lastname(last, Case.DATIVE, g)
    except Exception:
        last_dat = last
    surname_io = f"{last_dat} {first[0].upper()}.{patr[0].upper()}."
    return surname_io, ending


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
