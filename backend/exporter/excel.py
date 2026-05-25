"""Excel exporter with 8 sheets."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List
import re

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
from openpyxl.utils import get_column_letter


HEADERS = [
    ("№", 6),
    ("Компания", 30),
    ("Сайт", 22),
    ("ИНН", 14),
    ("КПП", 12),
    ("Общий email", 26),
    ("Общий телефон", 20),
    ("ФИО (полностью)", 30),
    ("Фамилия", 18),
    ("Имя", 14),
    ("Отчество", 18),
    ("Пол", 6),
    ("Должность (как на сайте)", 32),
    ("Должность (норм.)", 28),
    ("Категория отрасли", 22),
    ("Метод нормализации", 14),
    ("Личный email", 26),
    ("Личный телефон", 20),
    ("Соцсети", 30),
    ("URL источника", 38),
    ("Язык", 8),
    ("Дата", 12),
    ("Статус", 10),
    ("Комментарий", 24),
]

SHEET_NAMES = [
    "Генеральные директора",
    "Финансовые директора",
    "Главные бухгалтеры",
    "Главные инженеры",
    "Остальные",
    "Все контакты",
    "Сводка",
    "Отчёт качества",
]

_HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
_HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
_ALIGN = Alignment(horizontal="left", vertical="center", wrap_text=True)
_BORDER = Border(
    left=Side(style="thin", color="D0D0D0"),
    right=Side(style="thin", color="D0D0D0"),
    top=Side(style="thin", color="D0D0D0"),
    bottom=Side(style="thin", color="D0D0D0"),
)
_PARTIAL_FILL = PatternFill("solid", fgColor="FEF3C7")
_ERROR_FILL = PatternFill("solid", fgColor="FEE2E2")


def _setup_sheet(ws):
    for col_idx, (name, width) in enumerate(HEADERS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=name)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = _ALIGN
        cell.border = _BORDER
        ws.column_dimensions[get_column_letter(col_idx)].width = width
    ws.freeze_panes = "A2"
    ws.row_dimensions[1].height = 24


def _join_multi(value) -> str:
    """Join list/tuple/set into 'a; b; c'. Accepts str/None as-is.

    Preserves order, removes duplicates and empties.
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        seen, out = set(), []
        for v in value:
            if v is None:
                continue
            s = str(v).strip()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
        return "; ".join(out)
    return str(value)


# --- HOTFIX12-001: phone/email cell formatting -------------------------------
# Telephones: digits only, drop extensions (доб./ext./#NNN/x123/вн.).
# Emails: lowercase, dedup, '; ' separator. See HOTFIX12-001 in tech log.
_PHONE_DIGITS_RE = re.compile(r"\D+")
_PHONE_EXT_RE = re.compile(
    r"\s*(?:доб|ext|extension|x|#|вн|внутр)\.?\s*\d+\s*$",
    re.IGNORECASE,
)


def _join_phones(value) -> str:
    """Phones for Excel cell: digits only, drop extensions, '; ' separator, dedup."""
    if not value:
        return ""
    if isinstance(value, (list, tuple, set)):
        parts = list(value)
    else:
        parts = re.split(r"[;,\n]", str(value))
    out, seen = [], set()
    for p in parts:
        if p is None:
            continue
        s = str(p).strip()
        if not s:
            continue
        s = _PHONE_EXT_RE.sub("", s)
        digits = _PHONE_DIGITS_RE.sub("", s)
        if not digits or digits in seen:
            continue
        seen.add(digits)
        out.append(digits)
    return "; ".join(out)


def _join_emails(value) -> str:
    """Emails for Excel cell: lowercase, '; ' separator, dedup."""
    if not value:
        return ""
    if isinstance(value, (list, tuple, set)):
        parts = list(value)
    else:
        parts = re.split(r"[;,\n]", str(value))
    out, seen = [], set()
    for e in parts:
        if e is None:
            continue
        s = str(e).strip().lower()
        if not s or "@" not in s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return "; ".join(out)
# --- /HOTFIX12-001 ------------------------------------------------------------


def _contact_row(c: Dict, n: int) -> list:
    socials = c.get("social_links") or []
    if isinstance(socials, list):
        socials = "\n".join(socials)
    return [
        n,
        c.get("company_name") or "",
        c.get("domain") or "",
        c.get("inn") or "",
        c.get("kpp") or "",
        _join_emails(c.get("company_email")),
        _join_phones(c.get("company_phone")),
        c.get("full_name") or "",
        c.get("last_name") or "",
        c.get("first_name") or "",
        c.get("patronymic") or "",
        c.get("gender") or "",
        c.get("position_raw") or "",
        c.get("position_canonical") or "",
        c.get("role_category") or "",
        c.get("norm_method") or "",
        _join_emails(c.get("person_email")),
        _join_phones(c.get("person_phone")),
        socials,
        c.get("page_url") or "",
        c.get("language") or "",
        (c.get("extracted_at") or "")[:10],
        c.get("status") or "ok",
        c.get("notes") or "",
    ]


def _apply_row_style(ws, row_idx: int, status: str):
    fill = None
    if status == "partial":
        fill = _PARTIAL_FILL
    elif status == "error":
        fill = _ERROR_FILL
    for col in range(1, len(HEADERS) + 1):
        cell = ws.cell(row=row_idx, column=col)
        cell.alignment = _ALIGN
        cell.border = _BORDER
        if fill:
            cell.fill = fill


def _route_sheet(c: Dict) -> str:
    sheet = c.get("sheet") or ""
    if sheet in SHEET_NAMES:
        return sheet
    cat = (c.get("role_category") or "").lower()
    if "финанс" in cat:
        return "Финансовые директора"
    if "бухгалт" in cat:
        return "Главные бухгалтеры"
    if "инжен" in cat or "технич" in cat:
        return "Главные инженеры"
    if "ген" in cat or "директор" in cat:
        return "Генеральные директора"
    return "Остальные"


def export_to_xlsx(contacts: List[Dict], output_path: str, task_meta: Dict | None = None) -> str:
    wb = Workbook()
    # Remove default sheet
    wb.remove(wb.active)

    sheets = {}
    for name in SHEET_NAMES:
        ws = wb.create_sheet(title=name)
        if name not in ("Сводка", "Отчёт качества"):
            _setup_sheet(ws)
        sheets[name] = ws

    # Counters by sheet
    counters: Dict[str, int] = {n: 0 for n in SHEET_NAMES}

    # Fill specialized sheets + "Все контакты"
    all_ws = sheets["Все контакты"]
    for c in contacts:
        target = _route_sheet(c)
        ws = sheets.get(target) or sheets["Остальные"]
        counters[target] = counters.get(target, 0) + 1
        row_idx = counters[target] + 1
        ws.append(_contact_row(c, counters[target]))
        _apply_row_style(ws, row_idx, c.get("status") or "ok")

        counters["Все контакты"] = counters.get("Все контакты", 0) + 1
        all_row = counters["Все контакты"] + 1
        all_ws.append(_contact_row(c, counters["Все контакты"]))
        _apply_row_style(all_ws, all_row, c.get("status") or "ok")

    # Summary sheet
    summary = sheets["Сводка"]
    summary.cell(row=1, column=1, value="Параметр").font = _HEADER_FONT
    summary.cell(row=1, column=2, value="Значение").font = _HEADER_FONT
    summary.cell(row=1, column=1).fill = _HEADER_FILL
    summary.cell(row=1, column=2).fill = _HEADER_FILL
    summary.column_dimensions["A"].width = 36
    summary.column_dimensions["B"].width = 36

    meta = task_meta or {}
    rows = [
        ("Дата экспорта", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ("ID задачи", str(meta.get("task_id") or "")),
        ("Режим", str(meta.get("mode") or "")),
        ("Всего сайтов", str(meta.get("total_sites") or "")),
        ("Успешно обработано", str(meta.get("done_sites") or "")),
        ("С ошибкой", str(meta.get("failed_sites") or "")),
        ("Всего контактов", str(len(contacts))),
    ]
    for name in SHEET_NAMES:
        if name in ("Сводка", "Отчёт качества", "Все контакты"):
            continue
        rows.append((f"Лист «{name}»", str(counters.get(name, 0))))

    for i, (k, v) in enumerate(rows, start=2):
        summary.cell(row=i, column=1, value=k)
        summary.cell(row=i, column=2, value=v)
        for col in (1, 2):
            summary.cell(row=i, column=col).alignment = _ALIGN
            summary.cell(row=i, column=col).border = _BORDER

    # Quality report
    qual = sheets["Отчёт качества"]
    qual.cell(row=1, column=1, value="Метрика").font = _HEADER_FONT
    qual.cell(row=1, column=2, value="Значение").font = _HEADER_FONT
    qual.cell(row=1, column=1).fill = _HEADER_FILL
    qual.cell(row=1, column=2).fill = _HEADER_FILL
    qual.column_dimensions["A"].width = 40
    qual.column_dimensions["B"].width = 20

    total = len(contacts)
    with_fio = sum(1 for c in contacts if c.get("full_name"))
    with_position = sum(1 for c in contacts if c.get("position_canonical"))
    with_phone = sum(1 for c in contacts if c.get("person_phone") or c.get("company_phone"))
    with_email = sum(1 for c in contacts if c.get("person_email") or c.get("company_email"))
    with_inn = sum(1 for c in contacts if c.get("inn"))
    partial = sum(1 for c in contacts if c.get("status") == "partial")
    errors = sum(1 for c in contacts if c.get("status") == "error")

    methods = Counter((c.get("norm_method") or "unknown") for c in contacts)

    qrows = [
        ("Всего контактов", total),
        ("С распознанным ФИО", with_fio),
        ("С нормализованной должностью", with_position),
        ("С телефоном", with_phone),
        ("С email", with_email),
        ("С ИНН компании", with_inn),
        ("Частичные (partial)", partial),
        ("С ошибкой (error)", errors),
        ("—", "—"),
        ("Метод нормализации:", ""),
    ]
    for method, count in methods.most_common():
        qrows.append((f"  • {method}", count))

    for i, (k, v) in enumerate(qrows, start=2):
        qual.cell(row=i, column=1, value=k)
        qual.cell(row=i, column=2, value=v)
        for col in (1, 2):
            qual.cell(row=i, column=col).alignment = _ALIGN
            qual.cell(row=i, column=col).border = _BORDER

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    return output_path
