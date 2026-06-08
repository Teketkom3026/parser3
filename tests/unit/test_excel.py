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
