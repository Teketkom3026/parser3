"""Phone/email extraction tests."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.normalizer.phone import extract_phones
from backend.normalizer.email import extract_emails, classify_email


def test_extract_phones_ru():
    phones = extract_phones("Звоните: +7 (495) 123-45-67 или 8 800 555 35 35")
    assert len(phones) >= 1


def test_extract_emails():
    emails = extract_emails("Напишите на info@example.ru или sales@test.com")
    assert "info@example.ru" in emails
    assert "sales@test.com" in emails


def test_classify_email_general():
    assert classify_email("info@example.ru") == "general"
    assert classify_email("contact@example.ru") == "general"


def test_classify_email_personal():
    assert classify_email("ivanov@example.ru") == "personal"
