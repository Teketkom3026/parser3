"""site_processor: company-only fallback, target_positions filter, company_info merge."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.pipeline.site_processor import (
    _has_any_company_data,
    _make_company_only_record,
    _matches_target_positions,
    _merge_company_info,
)


def test_has_any_company_data():
    assert not _has_any_company_data({})
    assert not _has_any_company_data({"language": "ru"})
    assert _has_any_company_data({"company_name": "ООО Ромашка"})
    assert _has_any_company_data({"inn": "7701234567"})
    assert _has_any_company_data({"company_email": "info@example.com"})
    assert _has_any_company_data({"company_phone": "+79998887766"})


def test_make_company_only_record():
    company = {
        "company_name": "ООО Ромашка",
        "company_email": "info@romashka.ru",
        "company_phone": "+7 495 123-45-67",
        "inn": "7701234567",
        "kpp": "770101001",
        "language": "ru",
        "domain": "romashka.ru",
    }
    rec = _make_company_only_record(company, "https://romashka.ru/")
    assert rec["role_category"] == "Компания (без контактного лица)"
    assert rec["sheet_name"] == "Остальные"
    assert rec["status"] == "partial"
    assert rec["comment"] == "Компания без персональных ФИО"
    assert rec["full_name"] == ""
    assert rec["company_name"] == "ООО Ромашка"
    assert rec["inn"] == "7701234567"
    assert rec["norm_method"] == "empty"


def test_matches_target_positions_empty_list_keeps_all():
    assert _matches_target_positions({"position_raw": "anything"}, []) is True
    assert _matches_target_positions({"position_raw": "anything"}, None or []) is True


def test_matches_target_positions_canonical_match():
    c = {"position_raw": "Gl. bukhgalter", "position_canonical": "Главный бухгалтер"}
    assert _matches_target_positions(c, ["главный бухгалтер"]) is True


def test_matches_target_positions_raw_match():
    c = {"position_raw": "Главный бухгалтер ООО Ромашка", "position_canonical": ""}
    assert _matches_target_positions(c, ["главный бухгалтер"]) is True


def test_matches_target_positions_no_match():
    c = {"position_raw": "Разработчик", "position_canonical": "Разработчик"}
    assert _matches_target_positions(c, ["главный бухгалтер"]) is False


def test_merge_company_info_preserves_existing():
    base = {"inn": "111", "company_name": ""}
    fresh = {"inn": "222", "company_name": "ООО X", "kpp": "999"}
    _merge_company_info(base, fresh)
    assert base["inn"] == "111"  # existing not overwritten
    assert base["company_name"] == "ООО X"  # filled
    assert base["kpp"] == "999"  # filled


def test_company_only_dedup_key_non_empty():
    rec = _make_company_only_record(
        {"company_name": "ООО Ромашка", "domain": "romashka.ru"},
        "https://romashka.ru/",
    )
    assert rec.get("dedup_key"), "dedup_key must be set on company-only record"
