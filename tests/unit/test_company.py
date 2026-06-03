"""Company info: общий телефон/email из footer-class/id, header, шапки текста (П.6)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.extractor.company import extract_company_info


def test_footer_by_class():
    i = extract_company_info(
        '<div class="site-footer">Тел: +7 (812) 555-12-34, info@firma.ru</div>',
        "https://firma.ru",
    )
    assert i["company_phone"] == "78125551234"
    assert i["company_email"] == "info@firma.ru"


def test_footer_by_id():
    i = extract_company_info(
        '<div id="footer">8 (495) 100-20-30 zakaz@shop.ru</div>', "https://shop.ru"
    )
    assert i["company_phone"] == "74951002030"
    assert i["company_email"] == "zakaz@shop.ru"


def test_header_used_when_no_footer():
    i = extract_company_info(
        "<header>Звоните: +7 921 000 11 22, sales@co.ru</header><div>прочий текст</div>",
        "https://co.ru",
    )
    assert i["company_phone"] == "79210001122"
    assert i["company_email"] == "sales@co.ru"


def test_accumulates_across_scopes():
    """Footer без email → email добирается из header (накопительный поиск)."""
    i = extract_company_info(
        "<header>info@head.ru</header><footer>+7 (812) 777-88-99</footer>", "https://x.ru"
    )
    assert i["company_email"] == "info@head.ru"
    assert i["company_phone"] == "78127778899"
