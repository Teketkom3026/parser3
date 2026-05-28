"""Position normalization via pymorphy3 + catalog + rapidfuzz."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from backend.catalog.loader import PositionEntry, get_catalog
from backend.normalizer.morph import get_morph


@dataclass
class NormalizedPosition:
    canonical: str = ""
    category: str = "Другое"
    matched_id: Optional[str] = None
    method: str = "empty"   # exact / morph / fuzzy / fallback / empty
    sheet: Optional[str] = None
    raw_cleaned: str = ""


_ABBREVS = {"ceo", "cto", "cfo", "coo", "cio", "hr", "it", "ит", "pr", "gr", "vp", "cmo", "cro", "cso", "pmo"}
_OPF_WORDS = {"ооо", "оао", "пао", "зао", "ао", "ип", "фгуп", "муп", "гуп", "нко", "ано", "ltd", "llc", "inc", "gmbh"}

_POS_KEYWORDS = [
    "директор", "бухгалтер", "инженер", "менеджер", "руководитель",
    "начальник", "президент", "специалист", "мастер", "технолог",
    "врач", "юрист", "архитектор", "оператор", "инспектор",
    "ректор", "профессор", "советник", "помощник", "казначей",
    "аудитор", "разработчик", "программист", "рекрутер",
    "учредитель", "владелец", "основатель",
]


def _clean(raw: str) -> str:
    if not raw:
        return ""
    s = raw.strip(" \t\n\r-–—•·|,.;:")
    # First sentence cut
    s = re.split(r"[.!?;]\s+[А-ЯA-Z]", s, maxsplit=1)[0]
    s = re.sub(r"\s*[—–-]\s+[а-яa-z]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > 120:
        # Cut to first position keyword + 5 words after
        low = s.lower()
        for kw in _POS_KEYWORDS:
            m = re.search(rf"\b{re.escape(kw)}\w*\b", low)
            if m:
                # approximate word cut in original
                tail_start = m.end()
                tail = s[tail_start:].split()
                s = s[:tail_start] + " " + " ".join(tail[:6])
                break
        s = s[:120].strip()
    return s.strip(" \t\n\r-–—•·|,.;:")


def _reinflect_to_nom(phrase: str) -> str:
    """pymorphy3 each token → nominative singular. Keep abbreviations and quoted tokens as-is."""
    morph = get_morph()
    out_parts = []
    for tok in re.split(r"(\s+|[,.;:/])", phrase):
        if not tok or not tok.strip() or not re.match(r"[A-Za-zА-Яа-яЁё]", tok):
            out_parts.append(tok)
            continue
        low = tok.lower().strip(",.;:/")
        if low in _ABBREVS:
            out_parts.append(tok.upper())
            continue
        if low in _OPF_WORDS:
            out_parts.append(tok.upper())
            continue
        try:
            p = morph.parse(low)[0]
            pos_tag = getattr(p.tag, "POS", None)
            if pos_tag in ("NOUN", "ADJF", "ADJS", "PRTF"):
                infl = p.inflect({"nomn", "sing"})
                if infl and infl.word:
                    out_parts.append(infl.word)
                    continue
                infl = p.inflect({"nomn"})
                if infl and infl.word:
                    out_parts.append(infl.word)
                    continue
            out_parts.append(low)
        except Exception:
            out_parts.append(low)
    result = "".join(out_parts).strip()
    if result:
        result = result[:1].upper() + result[1:]
    return result


def _reinflect_to_dat(phrase: str) -> str:
    """Convert position phrase to dative.

    Inflects only nominative tokens to dative singular; non-nominative tokens
    (genitive, instrumental, already-dative prepositional tails, etc.) are kept
    as-is. This preserves «по продажам», «отдела продаж» and compound modifiers
    while converting the head noun phrase: «Генеральный директор» →
    «Генеральному директору».
    """
    morph = get_morph()
    out_parts = []
    for tok in re.split(r"(\s+|[,.;:/])", phrase):
        if not tok or not tok.strip() or not re.match(r"[A-Za-zА-Яа-яЁё]", tok):
            out_parts.append(tok)
            continue
        low = tok.lower().strip(",.;:/")
        if low in _ABBREVS or low in _OPF_WORDS:
            out_parts.append(tok.upper())
            continue
        try:
            p = morph.parse(low)[0]
            pos_tag = getattr(p.tag, "POS", None)
            case_tag = getattr(p.tag, "case", None)
            if pos_tag in ("NOUN", "ADJF", "ADJS", "PRTF") and case_tag == "nomn":
                infl = p.inflect({"datv", "sing"})
                if infl and infl.word:
                    w = infl.word
                    if tok[:1].isupper():
                        w = w[:1].upper() + w[1:]
                    out_parts.append(w)
                    continue
            out_parts.append(tok)
        except Exception:
            out_parts.append(tok)
    result = "".join(out_parts).strip()
    if result:
        result = result[:1].upper() + result[1:]
    return result


def _lemmas(text: str) -> set[str]:
    morph = get_morph()
    lemmas = set()
    for w in re.findall(r"[A-Za-zА-Яа-яЁё]+", text.lower()):
        try:
            parses = morph.parse(w)
            # Collect ALL normal_forms (multiple parses: adjective vs noun etc.)
            for p in parses[:3]:
                if p.normal_form:
                    lemmas.add(p.normal_form)
        except Exception:
            lemmas.add(w)
    return lemmas


_EXCLUDE_RE_CACHE: dict[str, re.Pattern] = {}


def _has_exclude(raw_low: str, modifiers: list[str]) -> bool:
    if not modifiers:
        return False
    key = "|".join(sorted(modifiers))
    pat = _EXCLUDE_RE_CACHE.get(key)
    if pat is None:
        # "зам" matches "заместитель", "зам.", "зама" — use prefix-word match
        parts = []
        for m in modifiers:
            esc = re.escape(m)
            parts.append(rf"\b{esc}\w*")
        pat = re.compile("|".join(parts), re.IGNORECASE)
        _EXCLUDE_RE_CACHE[key] = pat
    return bool(pat.search(raw_low))


_REQUIRES_CACHE: dict[str, re.Pattern] = {}


def _has_any_required(raw_low: str, tokens: list[str], lemmas: set[str] | None = None) -> bool:
    if not tokens:
        return True
    # Match against pre-computed lemmas if provided (more robust for падежи)
    if lemmas is not None:
        for t in tokens:
            tl = t.lower()
            # If token contains non-letter (e.g. "и.о.") — fall through to regex
            if not re.match(r"^[A-Za-zА-Яа-яЁё\-]+$", tl):
                continue
            if tl in lemmas:
                return True
            if len(tl) >= 4:
                for lem in lemmas:
                    if len(lem) >= 4 and (lem.startswith(tl) or tl.startswith(lem)):
                        return True
    key = "|".join(sorted(tokens))
    pat = _REQUIRES_CACHE.get(key)
    if pat is None:
        parts = [rf"\b{re.escape(t)}" for t in tokens]  # prefix match (no \w*)
        pat = re.compile("|".join(parts), re.IGNORECASE)
        _REQUIRES_CACHE[key] = pat
    return bool(pat.search(raw_low))


def normalize_position(raw: str) -> NormalizedPosition:
    """Normalize position string and return canonical in dative case."""
    result = _normalize_raw(raw)
    if result.canonical:
        result.canonical = _reinflect_to_dat(result.canonical)
    return result


def _normalize_raw(raw: str) -> NormalizedPosition:
    cleaned = _clean(raw or "")
    if not cleaned:
        return NormalizedPosition(method="empty", raw_cleaned="")

    cat = get_catalog()
    positions = cat.positions
    cleaned_low = cleaned.lower()

    # Pre-compute lemmas once (used by multiple steps)
    lemmas = _lemmas(cleaned)

    # 1. Exact alias match
    for entry in positions:
        if cleaned_low == entry.canonical.lower() or any(cleaned_low == a.lower() for a in entry.aliases):
            sheet = _apply_sheet(entry, cleaned_low, lemmas)
            return NormalizedPosition(
                canonical=entry.canonical, category=entry.category,
                matched_id=entry.id, method="exact", sheet=sheet,
                raw_cleaned=cleaned,
            )

    # 2. Lemmatize → keyword match (AND). Collect all candidates, pick max priority.
    matches: list[PositionEntry] = []
    for entry in positions:
        req = [w.lower() for w in entry.pymorphy_keywords]
        if not req:
            continue
        ok = True
        for r in req:
            if r in lemmas:
                continue
            # Only allow startswith when prefix is >=4 chars (avoid "по"→"помощник")
            matched = False
            for lem in lemmas:
                if len(lem) < 4 or len(r) < 4:
                    continue
                if lem.startswith(r) or r.startswith(lem):
                    matched = True
                    break
            if matched:
                continue
            ok = False
            break
        if ok and _has_any_required(cleaned_low, [x.lower() for x in entry.requires_any], lemmas):
            matches.append(entry)
    if matches:
        best = max(matches, key=lambda e: (e.priority, len(" ".join(e.pymorphy_keywords))))
        # Map to the canonical of catalog (not inflected free-form), but keep original tail for complex phrases
        canonical = _build_canonical(best, cleaned)
        sheet = _apply_sheet(best, cleaned_low, lemmas)
        return NormalizedPosition(
            canonical=canonical, category=best.category,
            matched_id=best.id, method="morph", sheet=sheet,
            raw_cleaned=cleaned,
        )

    # 3. Fuzzy — only on full canonical (not short aliases)
    try:
        from rapidfuzz import fuzz
        best_fuzzy = None
        best_score = 0
        for entry in positions:
            # skip too-generic single-word aliases
            candidates_for_fuzzy = [entry.canonical]
            candidates_for_fuzzy += [a for a in entry.aliases if len(a.split()) >= 2]
            for alias in candidates_for_fuzzy:
                s = fuzz.token_set_ratio(cleaned_low, alias.lower())
                if s > best_score:
                    best_score = s
                    best_fuzzy = entry
        if best_fuzzy and best_score >= 90:
            sheet = _apply_sheet(best_fuzzy, cleaned_low, lemmas)
            return NormalizedPosition(
                canonical=best_fuzzy.canonical, category=best_fuzzy.category,
                matched_id=best_fuzzy.id, method="fuzzy", sheet=sheet,
                raw_cleaned=cleaned,
            )
    except Exception:
        pass

    # 4. Fallback — pass cleaned as-is; wrapper applies _reinflect_to_dat preserving
    # existing non-nominative forms (e.g. «по продажам», «запасным частям»).
    return NormalizedPosition(
        canonical=cleaned, category="Другое",
        matched_id=None, method="fallback", sheet=None, raw_cleaned=cleaned,
    )


def _apply_sheet(entry: PositionEntry, cleaned_low: str, lemmas: set[str] | None = None) -> Optional[str]:
    if not entry.sheet:
        return None
    if _has_exclude(cleaned_low, [m.lower() for m in entry.exclude_modifiers]):
        return None
    # Empty requires_any means always ok
    if entry.requires_any and not _has_any_required(cleaned_low, [x.lower() for x in entry.requires_any], lemmas):
        return None
    return entry.sheet


def _build_canonical(entry: PositionEntry, cleaned: str) -> str:
    """Try to construct a natural canonical from the matched entry + tail modifiers.

    For complex phrases ("Заместитель генерального директора по финансам"):
    - If 'заместитель' / 'и.о.' is in raw → start with that modifier + genitive of canonical.
    - Else → return entry.canonical (clean).
    """
    raw_low = cleaned.lower()
    # simple case
    prefix = ""
    if re.match(r"\s*зам(?:\.|естител\w*|\s|$)", raw_low):
        prefix = "Заместитель "
    elif re.search(r"\bи\.\s*о\.?|\bврио\b", raw_low):
        prefix = "И.о. "
    elif re.search(r"\bпомощник\w*", raw_low):
        prefix = "Помощник "
    elif re.search(r"\bассистент\w*", raw_low):
        prefix = "Ассистент "

    canonical = _expand_canonical(entry, cleaned)
    if prefix:
        gen = _to_genitive(entry.canonical)
        tail = _extract_tail(cleaned)
        return (prefix + gen + (" " + tail if tail else "")).strip()
    tail = _extract_tail(cleaned)
    if tail:
        return (canonical + " " + tail).strip()
    return canonical


def _to_genitive(phrase: str) -> str:
    morph = get_morph()
    out = []
    for tok in re.split(r"(\s+)", phrase):
        if not tok.strip():
            out.append(tok)
            continue
        low = tok.lower()
        if low in _ABBREVS:
            out.append(tok.upper())
            continue
        try:
            p = morph.parse(low)[0]
            infl = p.inflect({"gent", "sing"})
            if infl and infl.word:
                w = infl.word
                if tok[:1].isupper():
                    w = w[:1].upper() + w[1:]
                out.append(w)
                continue
        except Exception:
            pass
        out.append(tok.lower())
    res = "".join(out)
    # Lowercase first letter (as we expect "заместитель генерального директора")
    if res:
        res = res[:1].lower() + res[1:]
    return res


_TAIL_RE = re.compile(r"\b(по|в|на|при)\s+[а-яё][а-яё\s-]{2,40}", re.IGNORECASE)

_DEPT_NOUNS = frozenset({
    "отдел", "служба", "центр", "управление", "департамент",
    "подразделение", "группа", "сектор", "дирекция", "отделение",
})

_PREP_STOP = frozenset({"по", "в", "на", "при", "для", "от", "за", "с", "из", "к", "у"})


def _extract_tail(phrase: str) -> str:
    m = _TAIL_RE.search(phrase)
    if not m:
        return ""
    tail = m.group(0).strip()
    # Keep as-is — raw tail from site, dative conversion handled by _reinflect_to_dat
    return tail


def _expand_canonical(entry: "PositionEntry", cleaned: str) -> str:
    """Expand catalog canonical with adjective modifiers and trailing domain words from cleaned.

    Only applied when the last canonical word is a department noun (отдел, служба, etc.)
    to avoid including noise for titles like «Генеральный директор».

    Examples:
        "Начальник отдела" + "Начальник технического отдела" → "Начальник технического отдела"
        "Руководитель отдела" + "Руководитель отдела продаж"  → "Руководитель отдела продаж"
        "Генеральный директор" + "Генеральный директор компании" → "Генеральный директор" (no expand)
    """
    morph = get_morph()
    canonical = entry.canonical
    canon_words = canonical.split()
    if not canon_words:
        return canonical

    try:
        last_lemma = morph.parse(canon_words[-1].lower())[0].normal_form
    except Exception:
        return canonical
    if last_lemma not in _DEPT_NOUNS:
        return canonical

    # Lemmas of all canonical words
    canon_lemmas: set[str] = set()
    for cw in canon_words:
        try:
            canon_lemmas.add(morph.parse(cw.lower())[0].normal_form)
        except Exception:
            canon_lemmas.add(cw.lower())

    # Find positions of canonical lemmas in cleaned
    cleaned_words = cleaned.split()
    canon_positions: list[int] = []
    for i, w in enumerate(cleaned_words):
        w_clean = re.sub(r"[,.;:]", "", w).lower()
        try:
            nf = morph.parse(w_clean)[0].normal_form
            if nf in canon_lemmas:
                canon_positions.append(i)
        except Exception:
            pass

    if not canon_positions:
        return canonical

    # Words from first to last canonical match (fills in adjective modifiers between)
    first_idx, last_idx = canon_positions[0], canon_positions[-1]
    expanded = list(cleaned_words[first_idx: last_idx + 1])

    # Append up to 2 extra words after last canonical word (domain specifiers, e.g. "продаж")
    for w in cleaned_words[last_idx + 1: last_idx + 3]:
        if re.sub(r"[,.;:]", "", w).lower() in _PREP_STOP:
            break
        expanded.append(w)

    result = " ".join(expanded)
    return (result[:1].upper() + result[1:]) if result else canonical
