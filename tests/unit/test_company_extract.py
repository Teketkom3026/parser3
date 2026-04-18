"""Company info extraction: ИНН/КПП and clean_company_name UI-noise stripping."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.extractor.company import extract_company_info
from backend.normalizer.company import (
    clean_company_name, extract_inn_from_text, extract_kpp_from_text,
)


def test_inn_detection_patterns():
    # Standard
    assert "7701234567" in extract_inn_from_text("ИНН: 7701234567")
    # Dot separator
    assert "7701234567" in extract_inn_from_text("ИНН.7701234567")
    # No space
    assert "7701234567" in extract_inn_from_text("ИНН7701234567")
    # 12-digit (ИП)
    assert "770123456789" in extract_inn_from_text("ИНН 770123456789")
    # INN / inn variant
    assert "7701234567" in extract_inn_from_text("INN: 7701234567")


def test_kpp_detection_patterns():
    assert "770101001" in extract_kpp_from_text("КПП: 770101001")
    assert "770101001" in extract_kpp_from_text("КПП 770101001")
    assert "770101001" in extract_kpp_from_text("КПП.770101001")


def test_extract_company_info_from_requisites_page():
    html = """<html><body>
      <div class="footer">
        Общество с ограниченной ответственностью «Ромашка»<br>
        ИНН: 7701234567 КПП 770101001<br>
        Email: info@romashka.ru Тел: +7 (495) 123-45-67
      </div>
    </body></html>"""
    info = extract_company_info(html, "https://romashka.ru/requisites")
    assert info["inn"] == "7701234567"
    assert info["kpp"] == "770101001"


def test_clean_company_name_strips_ui_noise():
    # Full-string UI noise → empty
    assert clean_company_name("Наша команда") == ""
    assert clean_company_name("Контакты") == ""
    assert clean_company_name("О компании") == ""
    assert clean_company_name("Главная страница") == ""
    # Prefix stripping
    out = clean_company_name("Наша команда — ООО «Ромашка»")
    assert "Ромашка" in out or out == ""


def test_clean_company_name_too_short_returns_empty():
    # Empty and whitespace inputs must return empty
    assert clean_company_name("") == ""
    assert clean_company_name("   ") == ""
