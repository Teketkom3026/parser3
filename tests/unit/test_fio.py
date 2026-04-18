"""FIO validation tests."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.normalizer.fio import is_valid_person_name, split_fio_raw


def test_rejects_english_noise():
    assert not is_valid_person_name("About Us")
    assert not is_valid_person_name("Our Team")
    assert not is_valid_person_name("Contact Us")


def test_accepts_russian_name():
    assert is_valid_person_name("Иванов Иван Иванович")
    assert is_valid_person_name("Петров Петр")


def test_split_fio():
    parts = split_fio_raw("Иванов Иван Иванович")
    assert parts is not None
    last, first, middle = parts
    assert last == "Иванов"
    assert first == "Иван"
    assert middle == "Иванович"
