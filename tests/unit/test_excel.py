"""Экспортер: колонка «Имя Отчество» (письмо РФОП п.1) + выравнивание строки."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.exporter.excel import HEADERS, _contact_row

_HEADER_NAMES = [h[0] for h in HEADERS]
_COL = _HEADER_NAMES.index("Имя Отчество")


def _row(**over):
    base = {"first_name": "Иван", "patronymic": "Иванович", "last_name": "Иванов"}
    base.update(over)
    return _contact_row(base, 1)


def test_row_length_matches_headers():
    # Сдвиг колонки не должен рассыпать выравнивание — длина строки == числу заголовков.
    assert len(_contact_row({}, 1)) == len(HEADERS)


def test_imya_otchestvo_full():
    assert _row()[_COL] == "Иван Иванович"


def test_imya_otchestvo_only_first():
    assert _row(patronymic="")[_COL] == "Иван"


def test_imya_otchestvo_empty():
    assert _row(first_name="", patronymic="")[_COL] == ""


def test_imya_otchestvo_between_otchestvo_and_pol():
    # Колонка стоит между «Отчество» и «Пол».
    assert _HEADER_NAMES[_COL - 1] == "Отчество"
    assert _HEADER_NAMES[_COL + 1] == "Пол"


# ── A2 (письмо п.16): соцсети через «; », не через перенос строки ──────────────
_SOC = _HEADER_NAMES.index("Соцсети")


def test_socials_joined_with_semicolon():
    cell = _contact_row({"social_links": ["https://vk.com/a", "https://t.me/b"]}, 1)[_SOC]
    assert cell == "https://vk.com/a; https://t.me/b"
    assert "\n" not in cell


def test_socials_empty():
    assert _contact_row({"social_links": []}, 1)[_SOC] == ""
    assert _contact_row({}, 1)[_SOC] == ""


# ── Управляющие символы из скрапа (02.08: task 7b3f9c28c78b упала на экспорте) ──
_POS_COL = _HEADER_NAMES.index("Должность (как на сайте)")
_NOTE_COL = _HEADER_NAMES.index("Комментарий")


def test_control_chars_stripped_from_cells():
    """openpyxl бросает IllegalCharacterError на управляющих символах — вычищаем их.

    Реальный случай: «Заместитель гене\x0bрального директора» с сайта уронил ВЕСЬ
    экспорт задачи (56 247 сайтов) — файл не собрался вообще.
    """
    row = _contact_row({"position_raw": "Заместитель гене\x0bрального директора"}, 1)
    assert row[_POS_COL] == "Заместитель генерального директора"


def test_all_illegal_ranges_stripped():
    # Полный запрещённый набор Excel: \x00-\x08, \x0b-\x0c, \x0e-\x1f.
    dirty = "A" + "".join(chr(i) for i in list(range(0, 9)) + [11, 12] + list(range(14, 32))) + "B"
    assert _contact_row({"company_name": dirty}, 1)[1] == "AB"


def test_legal_whitespace_preserved():
    # \t \n \r — Excel разрешает, их не трогаем.
    assert _contact_row({"company_name": "A\tB\nC\rD"}, 1)[1] == "A\tB\nC\rD"


def test_overlong_cell_truncated():
    # Предел Excel на ячейку — 32767 символов.
    assert len(_contact_row({"notes": "я" * 40000}, 1)[_NOTE_COL]) == 32767


def test_export_survives_illegal_chars(tmp_path):
    """Сквозная проверка: с «грязным» контактом файл ДОЛЖЕН собраться."""
    from backend.exporter.excel import export_to_xlsx
    out = tmp_path / "out.xlsx"
    export_to_xlsx(
        [{"full_name": "Иванов Иван", "position_raw": "Заместитель гене\x0bрального директора",
          "sheet_name": "Остальные", "status": "ok"}],
        str(out), {"id": "t1"},
    )
    assert out.exists() and out.stat().st_size > 0
