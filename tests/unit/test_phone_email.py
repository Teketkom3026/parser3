"""Phone/email extraction tests."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.normalizer.phone import extract_phones, is_mobile_ru
from backend.normalizer.email import extract_emails, classify_email
from backend.extractor.company import extract_company_info


def test_extract_phones_ru():
    phones = extract_phones("Звоните: +7 (495) 123-45-67 или 8 800 555 35 35")
    assert len(phones) >= 1


def test_is_mobile_ru():
    assert is_mobile_ru("79161234567") is True       # +7 9XX (with CC)
    assert is_mobile_ru("9161234567") is True         # 9XX (no CC, defensive)
    assert is_mobile_ru("74951234567") is False       # городской (Москва)
    assert is_mobile_ru("78001234567") is False       # 8-800
    assert is_mobile_ru("") is False


def test_company_phone_prefers_landline():
    """DX2: сотовые (+7 9XX) не попадают в «Общий телефон» — берём стационарный."""
    html = "<html><body><footer>Тел: +7 (495) 123-45-67, моб. +7 916 123-45-67</footer></body></html>"
    info = extract_company_info(html, "https://example.ru")
    assert info["company_phone"] == "74951234567"


def test_company_phone_skips_mobile_only():
    """DX2: если в наличии только сотовый — «Общий телефон» остаётся пустым."""
    html = "<html><body><footer>Звоните: +7 916 123-45-67</footer></body></html>"
    info = extract_company_info(html, "https://example.ru")
    assert info["company_phone"] == ""


def test_extract_emails():
    emails = extract_emails("Напишите на info@example.ru или sales@test.com")
    assert "info@example.ru" in emails
    assert "sales@test.com" in emails


def test_classify_email_general():
    assert classify_email("info@example.ru") == "general"
    assert classify_email("contact@example.ru") == "general"


def test_classify_email_personal():
    assert classify_email("ivanov@example.ru") == "personal"
