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
    # E1/E3 (письмо п.4/п.13): частые отраслевые/ЛПР-должности вне исходного словаря
    "председатель", "управляющий", "геолог", "механик", "энергетик",
    "экономист", "маркшейдер", "прораб", "агроном", "диспетчер", "конструктор",
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

# FIO candidate: 2..4 words starting uppercase cyrillic or latin.
# Слово-имя: заглавная + строчные, с поддержкой дефис-составных частей, где часть
# ПОСЛЕ дефиса тоже может начинаться с заглавной (Абдул-Хамид, Ага-Заде, Мехти-Заде).
# Прежний класс продолжения `[a-zа-яё\-\.]` не включал заглавные → дефисное имя
# обрывалось на втором заглавном («Байбетиров Абдул-Хамид Сайпаевич» давал
# «Хамид Сайпаевич»; «генеральный директор» цеплялся к следующему человеку).
_FIO_WORD = r"[A-ZА-ЯЁ][a-zа-яё]{1,30}(?:-[A-ZА-ЯЁ]?[a-zа-яё]{1,30})*"
# Токен-продолжение: слово-имя/отчество ИЛИ инициалы («И.» / «И.И.»).
_FIO_NEXT = r"(?:" + _FIO_WORD + r"|[A-ZА-ЯЁ]\.[A-ZА-ЯЁ]?\.?)"
# \s+ → [^\S\n]+ : НЕ склеивать токены через перевод строки. В блочном fallback
# (`_extract_from_block`) регекс гоняется по многострочному тексту блока, и `\s+`
# склеивал слова с соседних строк в мусорное «ФИО» («…в г. Кирове\nОбразование:»
# → «Кирове Образование», «Астин\nЗачем»). flat-проход бьёт текст на строки заранее
# и не страдал — но fallback срабатывает, когда flat ничего не нашёл.
_FIO_CANDIDATE = re.compile(rf"{_FIO_WORD}(?:[^\S\n]+{_FIO_NEXT}){{1,3}}")

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


# Ролевой ящик ↔ должность: local-part кодирует роль (gendirector@ ↔ ген.директор).
# Каждая запись — (подстроки-должности — ВСЕ должны встретиться в должности,
# подстроки local-part — любая совпадает). Используется ТОЛЬКО когда в карточке
# несколько ролевых ящиков и эвристика «ровно один» спасовала (cigapan на Tilda:
# gendirector@ и glavbuh@ в одной зоне → раньше оба отбрасывались).
_ROLE_EMAIL_HINTS: list[tuple[tuple[str, ...], tuple[str, ...]]] = [
    (("генеральн",),         ("gendir", "gendirector", "gendirektor", "generaldir", "gendirex")),
    (("финанс",),            ("findir", "findirector", "fdir", "cfo")),
    (("главн", "бухгалтер"), ("glavbuh", "glbuh", "glavbukh", "mainbuh", "buhgalter")),
    (("главн", "инженер"),   ("glaving", "glavinzh", "glinzh", "chiefeng")),
    (("коммерческ",),        ("comdir", "kommdir", "komdir", "commercial")),
]


def _role_email_for_position(position: str, role_emails: List[str]) -> str:
    """Ролевой ящик, local-part которого совпадает с ДОЛЖНОСТЬЮ человека.

    Возвращает ящик только при ОДНОЗНАЧНОМ совпадении (ровно один подходит) —
    иначе «», чтобы не угадывать. Так gendirector@ достаётся ген.директору, даже
    если рядом в карточке лежит glavbuh@ (соседнего человека).
    """
    if not position or not role_emails:
        return ""
    pos_low = position.lower()
    for pos_subs, local_subs in _ROLE_EMAIL_HINTS:
        if all(s in pos_low for s in pos_subs):
            hits = [
                e for e in role_emails
                if any(ls in e.split("@", 1)[0].lower() for ls in local_subs)
            ]
            return hits[0] if len(hits) == 1 else ""
    return ""


def _personal_email_for(fio: str, scope_emails: List[str], card_emails: List[str],
                        position: str = "") -> str:
    """Личный email человека (E4, письмо п.18).

    1) email, кодирующий ФИО (фамилия/инициалы в local-part), — самый надёжный
       (ищем в широком scope).
    2) иначе ролевой ящик (buh@, comdir@) из ЕГО карточки: берём, только если в
       карточке ровно ОДИН не-«общий» ящик (не info/contact/sales/...), иначе не
       угадываем. Раньше такой ящик не совпадал с фамилией → уходил в «общие» и
       терялся, хотя в вёрстке он внутри карточки человека.
    3) если ролевых ящиков несколько — сопоставляем ящик с ДОЛЖНОСТЬЮ
       (gendirector@ ↔ ген.директор), берём только при однозначном совпадении.
    """
    _, named = split_emails(scope_emails, full_name=fio)
    if named:
        return named[0]
    _, role = split_emails(card_emails)   # role = не из _GENERAL_LOCALS
    if len(role) == 1:
        return role[0]
    return _role_email_for_position(position, role)


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
    # E4: блок = одна карточка → scope == card. Личный или ролевой ящик карточки.
    person_email = _personal_email_for(name, emails, emails, position_raw)

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


# Одиночное слово с заглавной (кириллица) — кандидат в «оторванную» фамилию.
_SINGLE_CYR_WORD_RE = re.compile(r"^[А-ЯЁ][а-яё\-]+$")


def _merge_surname_lines(lines: List[str]) -> List[str]:
    """B2: склеить строку-фамилию с идущей следом «Имя Отчество».

    Вёрстка rikor-electronics: <b>Шперлинг<br/>Андрей Васильевич</b> — после
    разрыва по <br/> фамилия оказывается на отдельной строке и теряется (один
    токен — не кандидат в ФИО). Если одиночное слово с заглавной идёт перед
    валидным 2-токенным именем И вместе они образуют валидное полное ФИО —
    объединяем (фамилия + Имя Отчество). Гейт `is_valid_person_name` на обоих
    отсекает ложные склейки (адрес/меню/город).

    Бонус: с восстановленной фамилией `split_emails` матчит личный ящик
    (emelyanov.ev@… → «Емельянов») — раньше он уходил в «общие» и отбрасывался.
    """
    out: List[str] = []
    i, n = 0, len(lines)
    while i < n:
        cur = lines[i].strip()
        nxt = lines[i + 1].strip() if i + 1 < n else ""
        if (cur and nxt
                and _SINGLE_CYR_WORD_RE.match(cur)
                and not _POS_RE.search(cur)
                and is_valid_person_name(nxt)
                and is_valid_person_name(cur + " " + nxt)):
            out.append(cur + " " + nxt)
            i += 2
            continue
        out.append(lines[i])
        i += 1
    return out


# П.3: marketing/CTA line starts — not a job title even if a position keyword
# appears later ("Вы можете…", "Если…", "Оставьте заявку…", "С 19 лет…").
_NON_POS_START_RE = re.compile(
    r"^(?:"
    r"вы|вам|вас|мы|нам|нас|я|мне|меня|"
    r"если|это|эта|этот|эти|чтобы|когда|как|где|почему|зачем|"
    r"можете|может|оставьте|оставить|отправьте|отправить|закажите|заказать|"
    r"получите|получить|узнайте|узнать|свяжитесь|связаться|звоните|позвоните|"
    r"пишите|напишите|заполните|заполнить|нажмите|выберите|укажите|введите|"
    r"задайте|приходите|приезжайте|записывайтесь|запишитесь"
    r")\b"
    r"|^[сc]\s+\d",          # "С 19 …" (Cyrillic с / Latin c) + number
    re.IGNORECASE,
)


def _line_has_fio(line: str) -> bool:
    """True if the line embeds a valid ФИО (used to reject «должность+ФИО» lines).

    Tries the greedy candidate first, then sliding 3-/2-token windows over the
    capitalised tokens, so a name after a leading position word is still found
    («Директор Иванов Иван Иванович» → «Иванов Иван Иванович»).
    """
    cleaned = _EMAIL_STRIP.sub(" ", line)
    m = _FIO_CANDIDATE.search(cleaned)
    if m and is_valid_person_name(m.group(0)):
        return True
    tokens = re.findall(r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё\-\.]+", cleaned)
    for size in (3, 2):
        for i in range(len(tokens) - size + 1):
            if is_valid_person_name(" ".join(tokens[i:i + size])):
                return True
    return False


def _is_pos_line(line: str) -> bool:
    s = line.strip()
    if not (_POS_RE.search(s) and len(s) < 120 and not is_valid_person_name(s)):
        return False
    # П.3: drop marketing/CTA lines (pronoun / imperative verb / "С 19 …").
    if _NON_POS_START_RE.match(s):
        return False
    # П.3: drop lines that embed a full ФИО («должность + ФИО» mashed) — otherwise
    # the whole line lands in «Должность (норм.)». The name-only pass still keeps
    # the person on high-score pages.
    if _line_has_fio(s):
        return False
    return True


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


def _next_card_start(lines: List[str], after: int, limit: int = 8) -> int:
    """Индекс начала СЛЕДУЮЩЕЙ карточки (строка-должность или строка-ФИО) после `after`.

    E4: ролевой ящик человека ищем в зоне его карточки, ограниченной следующим
    человеком/должностью — а не фиксированным числом строк (между именем и почтой
    бывают строки-лейблы «Телефон:»/«Почта:», ooostm). Если границы нет — `after+limit`.
    """
    for k in range(after + 1, min(after + 1 + limit, len(lines))):
        if _is_pos_line(lines[k]):
            return k
        m = _FIO_CANDIDATE.search(_EMAIL_STRIP.sub(" ", lines[k]))
        if m and is_valid_person_name(m.group(0)):
            return k
    return min(after + 1 + limit, len(lines))


def _position_from_same_line(line: str, fio: str) -> str:
    """Должность из остатка строки «Должность - ФИО» / «ФИО — Должность» (E3).

    rusada: «Главный юрист - Скуратовский Сергей Петрович» — должность и ФИО на
    одной строке, поэтому строка не опознаётся как чистая «строка-должность»
    (внутри ФИО) и должность терялась. Берём остаток после удаления ФИО, если он
    похож на должность и не является сам по себе ФИО.
    """
    rest = _EMAIL_STRIP.sub(" ", line).replace(fio, " ").strip(" -–—•·|:,\t")
    rest = re.sub(r"\s{2,}", " ", rest)
    if rest and len(rest) < 100 and _POS_RE.search(rest) and not is_valid_person_name(rest):
        return rest
    return ""


def _extract_flat_text(html_text: str, page_score: int = 0) -> List[RawContact]:
    """Sliding window over non-empty lines. Handles both position→FIO and FIO→position order.

    Forward scan is limited to 2 lines to avoid crossing card boundaries.
    When forward scan finds nothing, a backward scan (up to 3 lines) is tried —
    this covers Drupal/CMS layouts where the name appears above the job title.
    """
    html_text = _merge_tag_split_lines(html_text)
    lines = [l.strip() for l in re.split(r"\n|<br\s*/?>", html_text) if l.strip()]
    lines = _merge_surname_lines(lines)  # B2: «Фамилия\nИмя Отчество» → одна строка
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
            # Не воровать ВПЕРЁД ФИО, у которого СВОЯ строка-должность сразу следом
            # (дистанция 1): такой человек принадлежит своей должности, а текущая
            # строка — «сирота» от разорванного <br>/тегами титула ПРЕДЫДУЩего лица
            # («Заместитель\nгенерального директора» → хвост-сирота «генерального
            # директора» цеплял следующего Мартынова, у которого ниже «Руководитель
            # отдела продаж» — bautex). Без этого сирота плодит ложного гендира.
            if (fwd_fio is not None
                    and fwd_j + 1 < len(lines)
                    and _is_pos_line(lines[fwd_j + 1])):
                fwd_fio, fwd_j = None, None
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
                # E4: ролевой ящик берём из зоны карточки до следующего человека
                # (без bleed в соседнюю; учитывает строки-лейблы «Телефон:»/«Почта:»).
                card_emails = extract_emails("\n".join(lines[lo:_next_card_start(lines, hi)]))
                person_email = _personal_email_for(fio, emails, card_emails, line)
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
            # E3: «Должность <…> ФИО» на одной строке → вытащить должность.
            same_line_pos = _position_from_same_line(line, m.group(0))
            scope = " \n".join(lines[max(0, j - 1):j + 3])
            emails = extract_emails(scope)
            phones = extract_phones(scope)
            # CE-6b: держим ФИО без личных тел./почт, если строка — «Должность <колонка>
            # ФИО» (должность ПЕРЕД именем, tabular label→value, intell-stroy «Ключевые
            # лица»). Строки-биографии «Иван Иванович проработал…» начинаются с ФИО и
            # сюда НЕ попадают (там «должность» — это хвост-предложение). page_score>=HIGH
            # уже отсекает обычные страницы.
            pos_before_name = bool(same_line_pos) and not line.lstrip().startswith(m.group(0))
            if not emails and not phones and not pos_before_name:
                continue
            # E4: scope уже тесный (j-1..j+3) → card == scope.
            person_email = _personal_email_for(m.group(0), emails, emails, same_line_pos)
            contacts.append(RawContact(
                full_name=m.group(0),
                position_raw=same_line_pos,
                person_email=person_email,
                person_phone=phones[0] if phones else "",
                source_block=scope[:500],
            ))
            used.add(j)
    return contacts


def _extract_kv_table_contacts(soup, page_url: str) -> List[RawContact]:
    """CE-6a: контакты из двухколоночных таблиц «метка → значение».

    Реквизиты-таблицы (dkc.ru): каждая `<tr>` — это `<td>должность</td><td>ФИО</td>`,
    т.е. должность и ФИО лежат В ОДНОЙ строке и спариваются однозначно. Плоский текст
    этого не видит: чередование «должность/ФИО» неотличимо от «ФИО/должность» (карточки
    mosgorlombard), и forward/backward-эвристика спаривает поперёк границ строк —
    «Главный бухгалтер» цеплял ФИО гендира из строки выше, а реальный главбух терялся.

    Таблицы, из которых что-то извлекли, удаляются из дерева — чтобы последующий
    flat-проход не переспаривал те же ячейки.
    """
    results: List[RawContact] = []
    for table in soup.find_all("table"):
        rows_out: List[RawContact] = []
        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"], recursive=False)
            if len(cells) != 2:
                continue
            key = cells[0].get_text(" ", strip=True)
            val = cells[1].get_text(" ", strip=True)
            if not _is_pos_line(key):
                continue
            m = _FIO_CANDIDATE.search(_EMAIL_STRIP.sub(" ", val))
            if not (m and is_valid_person_name(m.group(0))):
                continue
            emails = extract_emails(val)
            phones = extract_phones(val)
            rows_out.append(RawContact(
                full_name=m.group(0),
                position_raw=key.strip(" -–—•·|:"),
                person_email=_personal_email_for(m.group(0), emails, emails, key),
                person_phone=phones[0] if phones else "",
                page_url=page_url,
                source_block=(key + " | " + val)[:500],
            ))
        if rows_out:
            results.extend(rows_out)
            table.decompose()  # не отдавать те же ячейки flat-проходу
    return results


# Inline-теги форматирования: их текст принадлежит окружающему слову/строке, а не
# отдельной строке. <br> НЕ трогаем — на нём держится B2 (rikor «Фамилия<br>Имя О.»).
_INLINE_TAGS = [
    "b", "strong", "em", "i", "u", "span", "font", "mark", "small",
    "sub", "sup", "ins", "del", "s", "strike", "big", "tt", "abbr",
    "cite", "q", "var", "label", "bdi", "bdo",
]


def _unwrap_inline_tags(soup) -> None:
    """Снять inline-теги форматирования, склеив их текст с родителем.

    BeautifulSoup.get_text(separator="\\n") вставляет \\n между КАЖДЫМ текстовым
    узлом, в т.ч. между соседними inline-тегами. WYSIWYG-вёрстка (WordPress)
    разрывает должность на смежные теги без пробела:
    «<em>генеральн</em><em>ый директор</em>» → строки «генеральн» + «ый директор»,
    а ФИО в соседнем <b> — ещё одной строкой. Должность не опознаётся, осиротевший
    хвост цепляет чужого человека (atomsbyt: «генерального директора» прилипал к
    главбуху Зарницкой вместо реального гендира Рябцева).

    unwrap() оставляет соседние NavigableString раздельными узлами (get_text всё
    равно вставит \\n) — поэтому ОБЯЗАТЕЛЕН smooth(), он сливает смежные текстовые
    узлы в один. После этого текст внутри блока (p/div/li/td) — сплошной.
    """
    for name in _INLINE_TAGS:
        for tag in soup.find_all(name):
            tag.unwrap()
    soup.smooth()


def extract_raw_contacts(html: str, page_url: str = "", page_score: int = 0) -> List[RawContact]:
    from bs4 import BeautifulSoup
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    # Remove script/style/nav/footer for extraction
    for bad in soup(["script", "style", "nav", "header"]):
        bad.decompose()
    _unwrap_inline_tags(soup)

    seen = set()
    result: List[RawContact] = []

    # CE-6a: сначала двухколоночные таблицы «должность | ФИО» (надёжное пары-в-строке),
    # удаляя их из soup, чтобы flat не переспаривал. Делается ДО flat-прохода.
    for c in _extract_kv_table_contacts(soup, page_url):
        key = (c.full_name.lower(), c.position_raw.lower())
        if key in seen:
            continue
        seen.add(key)
        result.append(c)

    text = soup.get_text("\n", strip=True)
    # Main strategy: flat-text window (robust)
    flat = _extract_flat_text(text, page_score)

    # Dedup by (name, position_raw) first-pass
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
