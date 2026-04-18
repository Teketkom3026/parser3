"""Fixture-based integration test: furuno_team.html should yield >=10 contacts."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.extractor.contacts import extract_raw_contacts
from backend.extractor.company import extract_company_info
from backend.pipeline.site_processor import _normalize_contact


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "html" / "furuno_team.html"


def test_furuno_team_yields_contacts():
    if not FIXTURE.exists():
        import pytest
        pytest.skip("furuno_team.html fixture not present")
    html = FIXTURE.read_text(encoding="utf-8", errors="ignore")
    page_url = "https://furuno.ru/team/"
    company = extract_company_info(html, url=page_url)
    raw_list = extract_raw_contacts(html, page_url)
    contacts = []
    for raw in raw_list:
        c = _normalize_contact(raw, company, page_url)
        if c:
            contacts.append(c)
    assert len(contacts) >= 10, f"expected >=10 contacts, got {len(contacts)}"
    assert all(c.get("full_name") for c in contacts), "some contacts have no name"
    ceo_sheet = [c for c in contacts if c.get("sheet_name") == "Генеральные директора"]
    for c in ceo_sheet:
        pos_raw = (c.get("position_raw") or "").lower()
        assert "заместитель" not in pos_raw and "зам." not in pos_raw, \
            f"deputy in CEO sheet: {c}"
